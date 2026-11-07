from django.urls import path

from . import views

urlpatterns = [
    path("", views.upload, name="upload"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/review/", views.invoice_review, name="invoice_review"),
    path("invoices/<int:pk>/refresh/", views.invoice_refresh, name="invoice_refresh"),
    path("invoices/<int:pk>/remind/", views.invoice_remind, name="invoice_remind"),
    path("invoices/<int:pk>/delete/", views.invoice_delete, name="invoice_delete"),
    path("invoices/refresh/", views.invoice_refresh_all, name="invoice_refresh_all"),
    path("chat/", views.chat, name="chat"),
    path("chat/actions/<int:pk>/approve/", views.action_approve, name="action_approve"),
    path("chat/actions/<int:pk>/reject/", views.action_reject, name="action_reject"),
]