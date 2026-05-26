from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from unfold.admin import ModelAdmin, TabularInline

from bokio.exceptions import BokioError
from bokio.services import create_draft_for_lumber, push_lumber_to_invoice

from .models import Log, Lumber, Species


BTN_CLS = "bg-primary-600 hover:bg-primary-700 text-white rounded-md px-3 py-2 text-sm font-medium inline-block"


@admin.register(Species)
class SpeciesAdmin(ModelAdmin):
    list_display = ["name", "latin_name"]
    search_fields = ["name", "latin_name"]


class LumberInline(TabularInline):
    model = Lumber
    extra = 1
    fields = ["thickness_mm", "width_mm", "length_mm", "count", "status", "location", "notes"]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "length_mm":
            obj_id = request.resolver_match.kwargs.get("object_id") if request.resolver_match else None
            if obj_id:
                length_cm = Log.objects.filter(pk=obj_id).values_list("length_cm", flat=True).first()
                if length_cm:
                    kwargs["initial"] = length_cm * 10
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(Log)
class LogAdmin(ModelAdmin):
    list_display = [
        "mill_date",
        "species",
        "diameter_cm",
        "length_cm",
        "volume_m3_display",
        "lumber_volume_m3_display",
        "yield_pct_display",
        "fresh_blade_mounted",
        "source",
    ]
    list_filter = ["species", "mill_date", "source", "fresh_blade_mounted"]
    search_fields = ["notes", "source"]
    date_hierarchy = "mill_date"
    autocomplete_fields = ["species"]
    inlines = [LumberInline]

    fieldsets = (
        (None, {
            "fields": (
                "species",
                ("diameter_cm", "length_cm"),
                "source",
                ("received_date", "mill_date"),
                "fresh_blade_mounted",
                "notes",
            ),
        }),
        ("Beräknat", {
            "fields": (
                "volume_m3_display",
                "lumber_volume_m3_display",
                "yield_pct_display",
            ),
        }),
    )
    readonly_fields = ["volume_m3_display", "lumber_volume_m3_display", "yield_pct_display"]

    @admin.display(description="stock m³", ordering="diameter_cm")
    def volume_m3_display(self, obj: Log) -> str:
        v = obj.volume_m3
        return "—" if v is None else f"{v:.3f}"

    @admin.display(description="virke m³")
    def lumber_volume_m3_display(self, obj: Log) -> str:
        if obj.pk is None:
            return "—"
        return f"{obj.lumber_volume_m3:.3f}"

    @admin.display(description="avkastning %")
    def yield_pct_display(self, obj: Log) -> str:
        if obj.pk is None:
            return "—"
        y = obj.yield_pct
        return f"{y:.1f}%" if y is not None else "—"


class SoldFilter(admin.SimpleListFilter):
    title = "sålt?"
    parameter_name = "is_sold"

    def lookups(self, request, model_admin):
        return (("yes", "Sålt"), ("no", "Osålt"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(unit_price_sek__isnull=False)
        if self.value() == "no":
            return queryset.filter(unit_price_sek__isnull=True)
        return queryset


@admin.register(Lumber)
class LumberAdmin(ModelAdmin):
    list_display = [
        "log",
        "thickness_mm",
        "width_mm",
        "length_mm",
        "count",
        "status",
        "days_in_status_display",
        "location",
        "unit_price_sek",
        "revenue_sek_display",
        "bokio_invoice_id_short",
        "volume_m3_display",
    ]
    list_filter = [SoldFilter, "status", "location", "log__species"]
    list_editable = ["status", "location"]
    search_fields = ["notes", "location", "bokio_invoice_id"]
    autocomplete_fields = ["log"]
    actions = ["apply_suggested_price_action"]

    fieldsets = (
        (None, {
            "fields": (
                "log",
                ("thickness_mm", "width_mm", "length_mm"),
                "count",
                "status",
                "status_changed_at",
                "location",
                "notes",
            ),
        }),
        ("Pris & försäljning", {
            "fields": (
                "unit_price_sek",
                "suggested_price_sek_display",
                "use_suggested_price_button",
                "bokio_invoice_id",
                "create_bokio_draft_button",
                "push_to_bokio_button",
                "bokio_line_item_id",
            ),
        }),
    )
    readonly_fields = [
        "status_changed_at",
        "suggested_price_sek_display",
        "use_suggested_price_button",
        "create_bokio_draft_button",
        "push_to_bokio_button",
        "bokio_line_item_id",
    ]

    # ---- URL routing for the inline buttons --------------------------------

    def get_urls(self):
        custom = [
            path(
                "<path:object_id>/use-suggested-price/",
                self.admin_site.admin_view(self.use_suggested_price),
                name="mill_lumber_use_suggested_price",
            ),
            path(
                "<path:object_id>/push-to-bokio/",
                self.admin_site.admin_view(self.push_to_bokio),
                name="mill_lumber_push_to_bokio",
            ),
            path(
                "<path:object_id>/create-bokio-draft/",
                self.admin_site.admin_view(self.create_bokio_draft),
                name="mill_lumber_create_bokio_draft",
            ),
        ]
        return custom + super().get_urls()

    # ---- List-view displays ------------------------------------------------

    @admin.display(description="m³")
    def volume_m3_display(self, obj: Lumber) -> str:
        return f"{obj.volume_m3:.3f}"

    @admin.display(description="dagar", ordering="status_changed_at")
    def days_in_status_display(self, obj: Lumber) -> str:
        d = obj.days_in_status
        return "—" if d is None else str(d)

    @admin.display(description="intäkt (SEK)")
    def revenue_sek_display(self, obj: Lumber) -> str:
        r = obj.revenue_sek
        return "—" if r is None else f"{r}"

    @admin.display(description="Bokio")
    def bokio_invoice_id_short(self, obj: Lumber) -> str:
        return obj.bokio_invoice_id[:8] if obj.bokio_invoice_id else "—"

    # ---- Detail-view readonly displays -------------------------------------

    @admin.display(description="Föreslaget pris (ex moms)")
    def suggested_price_sek_display(self, obj: Lumber) -> str:
        if obj.pk is None or obj.thickness_mm is None or obj.width_mm is None or obj.length_mm is None:
            return "—"
        return f"{obj.suggested_price_sek} SEK"

    @admin.display(description="")
    def use_suggested_price_button(self, obj: Lumber) -> SafeString:
        if obj.pk is None:
            return mark_safe("—")
        url = reverse("admin:mill_lumber_use_suggested_price", args=[obj.pk])
        return format_html('<a href="{}" class="{}">Använd föreslaget pris</a>', url, BTN_CLS)

    @admin.display(description="")
    def create_bokio_draft_button(self, obj: Lumber) -> SafeString:
        if obj.pk is None:
            return mark_safe("—")
        if obj.bokio_invoice_id:
            return mark_safe("<em>Utkast redan kopplat.</em>")
        url = reverse("admin:mill_lumber_create_bokio_draft", args=[obj.pk])
        return format_html('<a href="{}" class="{}">Skapa Bokio-utkast</a>', url, BTN_CLS)

    @admin.display(description="")
    def push_to_bokio_button(self, obj: Lumber) -> SafeString:
        if obj.pk is None:
            return mark_safe("—")
        url = reverse("admin:mill_lumber_push_to_bokio", args=[obj.pk])
        return format_html('<a href="{}" class="{}">Skicka till Bokio</a>', url, BTN_CLS)

    # ---- Bulk action -------------------------------------------------------

    @admin.action(description="Sätt föreslaget pris (där tomt)")
    def apply_suggested_price_action(self, request, queryset):
        updated = 0
        for lumber in queryset.filter(unit_price_sek__isnull=True):
            lumber.unit_price_sek = lumber.suggested_price_sek
            lumber.save(update_fields=["unit_price_sek"])
            updated += 1
        self.message_user(request, f"{updated} virkesrad(er) prissatta.", messages.SUCCESS)

    # ---- Detail action handlers --------------------------------------------

    def use_suggested_price(self, request, object_id):
        lumber = self.get_object(request, object_id)
        redirect = HttpResponseRedirect(
            reverse("admin:mill_lumber_change", args=[object_id])
        )
        if lumber is None:
            return HttpResponseRedirect(reverse("admin:mill_lumber_changelist"))
        if lumber.unit_price_sek is not None:
            self.message_user(
                request, "Pris redan satt — lämnar oförändrat.", messages.WARNING
            )
        else:
            lumber.unit_price_sek = lumber.suggested_price_sek
            lumber.save(update_fields=["unit_price_sek"])
            self.message_user(
                request, f"Pris satt till {lumber.unit_price_sek} SEK.", messages.SUCCESS
            )
        return redirect

    def push_to_bokio(self, request, object_id):
        lumber = self.get_object(request, object_id)
        redirect = HttpResponseRedirect(
            reverse("admin:mill_lumber_change", args=[object_id])
        )
        if lumber is None:
            return redirect
        if not lumber.bokio_invoice_id:
            self.message_user(
                request,
                "Ange Bokio-faktura-id först (klistra in GUID från Bokio).",
                messages.ERROR,
            )
            return redirect
        if lumber.unit_price_sek is None:
            self.message_user(request, "Sätt pris först.", messages.ERROR)
            return redirect
        try:
            line_item_id = push_lumber_to_invoice(lumber, lumber.bokio_invoice_id)
        except BokioError as e:
            self.message_user(request, f"Bokio-fel: {e}", messages.ERROR)
            return redirect
        self.message_user(
            request,
            f"Skickat. Radobjekt {line_item_id or '(utan id)'}.",
            messages.SUCCESS,
        )
        return redirect

    def create_bokio_draft(self, request, object_id):
        lumber = self.get_object(request, object_id)
        redirect = HttpResponseRedirect(
            reverse("admin:mill_lumber_change", args=[object_id])
        )
        if lumber is None:
            return redirect
        if lumber.unit_price_sek is None:
            self.message_user(request, "Sätt pris först.", messages.ERROR)
            return redirect
        if lumber.bokio_invoice_id:
            self.message_user(
                request,
                "Bokio-utkast redan kopplat — använd 'Skicka till Bokio' för fler rader.",
                messages.WARNING,
            )
            return redirect
        try:
            invoice_id, line_item_id = create_draft_for_lumber(lumber)
        except BokioError as e:
            self.message_user(request, f"Bokio-fel: {e}", messages.ERROR)
            return redirect
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
            return redirect
        self.message_user(
            request,
            f"Utkast skapat: {invoice_id[:8]}… (radobjekt {line_item_id[:8] or '(?)'}).",
            messages.SUCCESS,
        )
        return redirect
