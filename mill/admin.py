from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Log, Lumber, Species


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
        "source",
    ]
    list_filter = ["species", "mill_date", "source"]
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
        if obj.diameter_cm is None or obj.length_cm is None:
            return "—"
        return f"{obj.volume_m3:.3f}"

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
        "volume_m3_display",
    ]
    list_filter = ["status", "location", "log__species"]
    list_editable = ["status", "location"]
    search_fields = ["notes", "location"]
    autocomplete_fields = ["log"]
    readonly_fields = ["status_changed_at"]

    @admin.display(description="m³")
    def volume_m3_display(self, obj: Lumber) -> str:
        return f"{obj.volume_m3:.3f}"

    @admin.display(description="dagar", ordering="status_changed_at")
    def days_in_status_display(self, obj: Lumber) -> str:
        d = obj.days_in_status
        return "—" if d is None else str(d)
