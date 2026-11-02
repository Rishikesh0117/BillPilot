from django.urls import path

from . import views

urlpatterns = [
    path("", views.upload, name="upload"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/review/", views.invoice_review, name="invoice_review"),
]