import math

from django.db import models
from django.utils import timezone


class Species(models.Model):
    name = models.CharField("namn", max_length=64, unique=True)
    latin_name = models.CharField("latinskt namn", max_length=128, blank=True)

    class Meta:
        verbose_name = "trädslag"
        verbose_name_plural = "trädslag"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Log(models.Model):
    species = models.ForeignKey(
        Species, on_delete=models.PROTECT, related_name="logs", verbose_name="trädslag"
    )
    diameter_cm = models.PositiveSmallIntegerField("toppdiameter (cm)")
    length_cm = models.PositiveSmallIntegerField("längd (cm)")
    source = models.CharField("ursprung", max_length=128, blank=True)
    received_date = models.DateField("mottaget", null=True, blank=True)
    mill_date = models.DateField("sågdatum")
    notes = models.TextField("anteckningar", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "stock"
        verbose_name_plural = "stockar"
        ordering = ["-mill_date", "-id"]

    def __str__(self) -> str:
        return f"#{self.pk} {self.species} {self.diameter_cm}x{self.length_cm}cm"

    @property
    def volume_m3(self) -> float:
        r_m = self.diameter_cm / 200
        l_m = self.length_cm / 100
        return math.pi * r_m * r_m * l_m

    @property
    def lumber_volume_m3(self) -> float:
        return sum((b.volume_m3 for b in self.lumber.all()), 0.0)

    @property
    def yield_pct(self) -> float | None:
        v = self.volume_m3
        if v <= 0:
            return None
        return self.lumber_volume_m3 / v * 100


class Lumber(models.Model):
    class Status(models.TextChoices):
        GREEN = "green", "färskt"
        DRYING = "drying", "torkar"
        DRY = "dry", "torrt"
        SOLD = "sold", "sålt / borta"

    log = models.ForeignKey(
        Log, on_delete=models.CASCADE, related_name="lumber", verbose_name="stock"
    )
    thickness_mm = models.PositiveSmallIntegerField("tjocklek (mm)")
    width_mm = models.PositiveSmallIntegerField("bredd (mm)")
    length_mm = models.PositiveIntegerField("längd (mm)")
    count = models.PositiveSmallIntegerField("antal", default=1)
    status = models.CharField(
        "status", max_length=16, choices=Status.choices, default=Status.GREEN
    )
    status_changed_at = models.DateTimeField("status ändrad", default=timezone.now)
    location = models.CharField("plats", max_length=64, blank=True)
    notes = models.TextField("anteckningar", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "virke"
        verbose_name_plural = "virke"
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.count}x {self.thickness_mm}x{self.width_mm}x{self.length_mm}mm"

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_status = instance.status
        return instance

    def save(self, *args, **kwargs):
        if self.pk and getattr(self, "_loaded_status", None) != self.status:
            self.status_changed_at = timezone.now()
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    @property
    def volume_m3(self) -> float:
        return self.thickness_mm * self.width_mm * self.length_mm * self.count / 1e9

    @property
    def days_in_status(self) -> int | None:
        if not self.status_changed_at:
            return None
        return (timezone.now() - self.status_changed_at).days
