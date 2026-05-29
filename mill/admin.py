from decimal import ROUND_HALF_UP, Decimal

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.widgets import UnfoldAdminDecimalFieldWidget

from bokio.exceptions import BokioError
from bokio.services import (
    create_draft_for_lumber,
    fetch_invoice_info,
    push_lumber_to_invoice,
)

from .models import Log, Lumber, Species

BTN_CLS = "bg-primary-600 hover:bg-primary-700 text-white rounded-md px-3 py-2 text-sm font-medium inline-block"

# Bokio invoice status enum -> Swedish label (see bokio company-api spec).
BOKIO_STATUS_SV = {
    "draft": "Utkast",
    "published": "Publicerad",
    "paid": "Betald",
    "overPaid": "Överbetald",
    "underPaid": "Delbetald",
    "overdue": "Förfallen",
    "credited": "Krediterad",
    "credit": "Kreditfaktura",
}


def _bokio_invoice_url(invoice_id: str) -> str:
    company = settings.BOKIO_COMPANY_ID
    if not invoice_id or not company:
        return ""
    return f"https://app.bokio.se/{company}/invoicing/invoices/view/{invoice_id}"


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


class LumberAdminForm(forms.ModelForm):
    total_price_sek = forms.DecimalField(
        label="Totalt (ex moms)",
        required=False,
        max_digits=12,
        decimal_places=2,
        widget=UnfoldAdminDecimalFieldWidget(),
        help_text="Fyll i totalpris om du föredrar att räkna baklänges. "
                  "Vid spar: om du ändrar detta fält räknas pris/styck om.",
    )

    class Meta:
        model = Lumber
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        inst = getattr(self, "instance", None)
        if inst is not None and inst.pk and inst.revenue_sek is not None:
            self.fields["total_price_sek"].initial = inst.revenue_sek

    def clean(self):
        cleaned = super().clean()
        if "total_price_sek" in self.changed_data:
            total = cleaned.get("total_price_sek")
            count = cleaned.get("count") or 1
            if total is None:
                cleaned["unit_price_sek"] = None
            elif count > 0:
                cleaned["unit_price_sek"] = (
                    Decimal(total) / Decimal(count)
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return cleaned


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
    form = LumberAdminForm
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
                ("unit_price_sek", "suggested_price_sek_display"),
                "total_price_sek",
                ("bokio_invoice_id", "bokio_invoice_link"),
                "bokio_status_display",
                "create_bokio_draft_button",
                "push_to_bokio_button",
                "bokio_line_item_id",
            ),
        }),
    )
    readonly_fields = [
        "status_changed_at",
        "suggested_price_sek_display",
        "bokio_invoice_link",
        "bokio_status_display",
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
    def bokio_invoice_id_short(self, obj: Lumber) -> SafeString:
        if not obj.bokio_invoice_id:
            return mark_safe("—")
        short = obj.bokio_invoice_id[:8]
        url = _bokio_invoice_url(obj.bokio_invoice_id)
        if not url:
            return format_html("{}", short)
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>', url, short
        )

    # ---- Detail-view readonly displays -------------------------------------

    @admin.display(description="Föreslaget pris (ex moms)")
    def suggested_price_sek_display(self, obj: Lumber) -> SafeString:
        if obj.pk is None or obj.thickness_mm is None or obj.width_mm is None or obj.length_mm is None:
            return mark_safe("—")
        url = reverse("admin:mill_lumber_use_suggested_price", args=[obj.pk])
        return format_html(
            '<span class="mr-3">{} SEK</span>'
            '<a href="{}" class="{}">Använd</a>',
            obj.suggested_price_sek, url, BTN_CLS,
        )

    @admin.display(description="")
    def bokio_invoice_link(self, obj: Lumber) -> SafeString:
        if obj.pk is None or not obj.bokio_invoice_id:
            return mark_safe("—")
        url = _bokio_invoice_url(obj.bokio_invoice_id)
        if not url:
            return mark_safe("—")
        return format_html(
            '<a href="{}" target="_blank" rel="noopener" class="{}">Öppna i Bokio</a>',
            url, BTN_CLS,
        )

    @admin.display(description="Bokio-status")
    def bokio_status_display(self, obj: Lumber) -> SafeString:
        if obj.pk is None or not obj.bokio_invoice_id:
            return mark_safe("—")
        try:
            info = fetch_invoice_info(obj.bokio_invoice_id)
        except BokioError as e:
            return format_html(
                '<span class="text-red-600">Kunde inte hämta från Bokio: {}</span>', str(e)
            )
        status = BOKIO_STATUS_SV.get(info.status, info.status or "—")
        rows = [("Status", status)]
        if info.customer_name:
            rows.append(("Kund", info.customer_name))
        if info.invoice_number:
            rows.append(("Fakturanr", info.invoice_number))
        if info.total_amount is not None:
            cur = f" {info.currency}" if info.currency else ""
            amount = f"{info.total_amount:g}{cur}"
            if info.paid_amount:
                amount += f" (betalt {info.paid_amount:g}{cur})"
            rows.append(("Belopp", amount))
        if info.due_date:
            rows.append(("Förfaller", info.due_date))
        body = format_html_join(
            "",
            '<tr><th class="text-left pr-3 font-medium align-top">{}</th><td>{}</td></tr>',
            rows,
        )
        return format_html('<table>{}</table>', body)

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
        if obj.bokio_line_item_id:
            return mark_safe("<em>Redan skickat till Bokio.</em>")
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
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
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
