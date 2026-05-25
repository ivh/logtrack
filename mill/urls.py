from django.urls import path

from . import views

app_name = "mill"

urlpatterns = [
    path("yield/", views.yield_report, name="yield_report"),
]
