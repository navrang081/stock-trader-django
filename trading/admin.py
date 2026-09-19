from django.contrib import admin
from .models import Stock, Wallet, Portfolio, Transaction, Order, SiteSettings, Basket, BasketAllocation, BasketItem

admin.site.register(Basket)
admin.site.register(BasketAllocation)
admin.site.register(BasketItem)

admin.site.register(Stock)
admin.site.register(Wallet)
admin.site.register(Portfolio)
admin.site.register(Transaction)
admin.site.register(Order)

@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # Sirf ek hi SiteSettings row allow karo (naya "Add" button hide kar do agar already ek row hai)
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Delete button bhi hide kar do, taaki galti se settings delete na ho
        return False