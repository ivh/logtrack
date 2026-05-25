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

    @admin.display(description="stock m³", ordering="diameter_cm")
    def volume_m3_display(self, obj: Log) -> str:
        return f"{obj.volume_m3:.3f}"

    @admin.display(description="virke m³")
    def lumber_volume_m3_display(self, obj: Log) -> str:
        return f"{obj.lumber_volume_m3:.3f}"

    @admin.display(description="avkastning %")
    def yield_pct_display(self, obj: Log) -> str:
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
        "location",
        "volume_m3_display",
    ]
    list_filter = ["status", "location", "log__species"]
    list_editable = ["status", "location"]
    search_fields = ["notes", "location"]
    autocomplete_fields = ["log"]

    @admin.display(description="m³")
    def volume_m3_display(self, obj: Lumber) -> str:
        return f"{obj.volume_m3:.3f}"
