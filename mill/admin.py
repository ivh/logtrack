from decimal import ROUND_HALF_UP, Decimal

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.widgets import (
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminExpandableTextareaWidget,
    UnfoldAdminSelectWidget,
)

from bokio.exceptions import BokioError
from bokio.services import (
    create_draft_for_lumber,
    fetch_invoice_info,
    list_draft_invoices,
    push_lumber_to_invoice,
)

from .models import Log, Lumber, LumberSource, Species

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


class LumberSourceForLogInline(TabularInline):
    """Batches drawn from this log (with the per-log board count)."""

    model = LumberSource
    fk_name = "log"
    extra = 0
    autocomplete_fields = ["lumber"]
    fields = ["lumber", "count"]
    verbose_name = "virkesparti från denna stock"
    verbose_name_plural = "virkespartier från denna stock"


class LumberSourceForLumberInline(TabularInline):
    """Source logs for this batch — add rows to combine several logs into one."""

    model = LumberSource
    fk_name = "lumber"
    extra = 1
    autocomplete_fields = ["log"]
    fields = ["log", "count"]
    verbose_name = "stockandel"
    verbose_name_plural = "stockandelar (lägg till stockar i partiet)"


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
    inlines = [LumberSourceForLogInline]

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
        widgets = {"notes": UnfoldAdminExpandableTextareaWidget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        inst = getattr(self, "instance", None)
        if inst is not None and inst.pk and inst.revenue_sek is not None:
            self.fields["total_price_sek"].initial = inst.revenue_sek
        self._init_invoice_picker(inst)

    def _init_invoice_picker(self, inst):
        # Offer a dropdown of Bokio draft invoices to push onto, but only before
        # this lumber has been pushed. Falls back to the plain text field (paste
        # a GUID) when Bokio is unreachable.
        if inst is None or not inst.pk or inst.bokio_line_item_id:
            return
        try:
            drafts = list_draft_invoices()
        except BokioError:
            return
        current = inst.bokio_invoice_id or ""
        choices = [("", "— välj utkast —")]
        choices += [(d.id, d.label) for d in drafts]
        if current and current not in {d.id for d in drafts}:
            choices.append((current, f"{current} (nuvarande)"))
        self.fields["bokio_invoice_id"] = forms.ChoiceField(
            label=self.fields["bokio_invoice_id"].label,
            required=False,
            choices=choices,
            initial=current,
            widget=UnfoldAdminSelectWidget(),
            help_text="Välj ett befintligt Bokio-utkast att lägga raden på.",
        )

    # total_price_sek -> unit_price_sek is derived in LumberAdmin.save_related,
    # once the source rows (and thus the board count) have been saved.


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
    inlines = [LumberSourceForLumberInline]
    list_display = [
        "dims_display",
        "count_display",
        "logs_display",
        "status",
        "days_in_status_display",
        "revenue_sek_display",
        "bokio_invoice_id_short",
        "volume_m3_display",
    ]
    list_display_links = ["dims_display"]
    list_filter = [SoldFilter, "status", "location", "logs__species"]
    list_editable = ["status"]
    search_fields = ["notes", "location", "bokio_invoice_id"]
    actions = ["apply_suggested_price_action"]

    fieldsets = (
        (None, {
            "fields": (
                ("thickness_mm", "width_mm", "length_mm"),
                ("status", "status_changed_at"),
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

    @admin.display(description="dim (mm)", ordering="thickness_mm")
    def dims_display(self, obj: Lumber) -> str:
        return f"{obj.thickness_mm}×{obj.width_mm}×{obj.length_mm}"

    @admin.display(description="m³")
    def volume_m3_display(self, obj: Lumber) -> str:
        return f"{obj.volume_m3:.3f}"

    @admin.display(description="stockar")
    def logs_display(self, obj: Lumber) -> str:
        return ", ".join(f"#{s.log_id}" for s in obj.sources.all()) or "—"

    @admin.display(description="antal")
    def count_display(self, obj: Lumber) -> int:
        return obj.count

    def save_related(self, request, form, formsets, change):
        # Source rows carry the board count, so derive unit price from a
        # typed-in total only after the inline formset has been saved.
        super().save_related(request, form, formsets, change)
        if "total_price_sek" not in form.changed_data:
            return
        total = form.cleaned_data.get("total_price_sek")
        obj = form.instance
        if total is None:
            obj.unit_price_sek = None
        else:
            count = obj.count
            if count <= 0:
                return
            obj.unit_price_sek = (Decimal(total) / Decimal(count)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        obj.save(update_fields=["unit_price_sek"])

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
                "Välj ett Bokio-utkast först (spara raden efter valet).",
                messages.ERROR,
            )
            return redirect
        if lumber.unit_price_sek is None:
            self.message_user(request, "Sätt pris först.", messages.ERROR)
            return redirect
        try:
            line_item_id, customer = push_lumber_to_invoice(lumber, lumber.bokio_invoice_id)
        except BokioError as e:
            self.message_user(request, f"Bokio-fel: {e}", messages.ERROR)
            return redirect
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
            return redirect
        to = f" till {customer}" if customer else ""
        self.message_user(
            request,
            f"Skickat{to}. Radobjekt {line_item_id or '(utan id)'}.",
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
