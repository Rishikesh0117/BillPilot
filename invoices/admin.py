from django.contrib import admin

from .models import Invoice, LineItem


class LineItemInline(admin.TabularInline):
    model = LineItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "client_email", "state", "paypal_status", "created_at")
    inlines = [LineItemInline]