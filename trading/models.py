from django.db import models
from django.contrib.auth.models import User

# 1. Stock — market me available stocks
class Stock(models.Model):
    symbol = models.CharField(max_length=10, unique=True)   # e.g. "TCS", "INFY"
    name = models.CharField(max_length=100)                  # e.g. "Tata Consultancy Services"
    current_price = models.DecimalField(max_digits=10, decimal_places=2)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.symbol} - ₹{self.current_price}"


# 2. Wallet — har user ka virtual balance
class Wallet(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=100000.00)
    daily_loss_limit = models.DecimalField(max_digits=12, decimal_places=2, default=10000.00)
    max_position_percent = models.DecimalField(max_digits=5, decimal_places=2, default=30.00)
    todays_loss = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    last_reset_date = models.DateField(auto_now_add=True)
    margin_used = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    short_margin_percent = models.DecimalField(max_digits=5, decimal_places=2, default=30.00)
    max_open_positions = models.IntegerField(default=5)
    is_trading_halted = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username}'s Wallet - ₹{self.balance}"


# 3. Portfolio — user ke paas kaunsa stock, kitni quantity
class Portfolio(models.Model):
    SL_TARGET_TYPE_CHOICES = [
        ('POINT', 'Point Wise'),
        ('PRICE', 'Price Wise'),
        ('PNL', 'P&L Wise'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    average_buy_price = models.DecimalField(max_digits=10, decimal_places=2)

    # Stop Loss (trailing)
    stop_loss_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stop_loss_type = models.CharField(max_length=10, choices=SL_TARGET_TYPE_CHOICES, null=True, blank=True)
    stop_loss_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_trailing_sl = models.BooleanField(default=False)
    sl_trail_distance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    highest_price_since_entry = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    lowest_price_since_entry = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    highest_pnl_since_entry = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Target (fixed)
    target_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    target_type = models.CharField(max_length=10, choices=SL_TARGET_TYPE_CHOICES, null=True, blank=True)
    target_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ('user', 'stock')

    def __str__(self):
        return f"{self.user.username} - {self.stock.symbol} x {self.quantity}"


# 4. Transaction — buy/sell history
class Transaction(models.Model):
    BUY = 'BUY'
    SELL = 'SELL'
    TRANSACTION_TYPES = [(BUY, 'Buy'), (SELL, 'Sell')]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    transaction_type = models.CharField(max_length=4, choices=TRANSACTION_TYPES)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transaction_type} {self.quantity} {self.stock.symbol} @ ₹{self.price}"
    # ---------------------------------------------------------
# 5. Order — Pending/Limit/SL/GTT/Bracket orders ke liye
# ---------------------------------------------------------
class Order(models.Model):
    ORDER_TYPES = [
        ('MARKET', 'Market'),
        ('LIMIT', 'Limit'),
        ('SL-M', 'Stop-Loss Market'),
        ('SL-L', 'Stop-Loss Limit'),
        ('GTT', 'Good Till Triggered'),
        ('AMO', 'After Market Order'),
        ('SCHEDULED', 'Scheduled Order'),
    ]
    SIDE_CHOICES = [('BUY', 'Buy'), ('SELL', 'Sell')]
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('EXECUTED', 'Executed'),
        ('CANCELLED', 'Cancelled'),
        ('REJECTED', 'Rejected'),
        ('EXPIRED', 'Expired'),
    ]
    VALIDITY_CHOICES = [('DAY', 'Day'), ('IOC', 'IOC'), ('GTT', 'GTT'), ('AMO', 'AMO - After Market'), ('SCHEDULED', 'Scheduled')]
    PRODUCT_TYPES = [('INTRADAY', 'Intraday'), ('NORMAL', 'Normal (Delivery)')]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    order_type = models.CharField(max_length=10, choices=ORDER_TYPES)
    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    quantity = models.PositiveIntegerField()

    # LIMIT / SL-L ke liye price
    limit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # SL-M / SL-L ke liye trigger price (jahan pahunchne par order activate ho)
    trigger_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    product_type = models.CharField(max_length=10, choices=PRODUCT_TYPES, default='NORMAL')
    slippage_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    reference_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    validity = models.CharField(max_length=10, choices=VALIDITY_CHOICES, default='DAY')
    execute_after = models.DateTimeField(null=True, blank=True)   # AMO / Scheduled ke liye
    expiry_date = models.DateField(null=True, blank=True)         # GTT ke liye

    # OCO (One Cancels Other) — dusre order se link
    linked_order = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='linked_from')
    # Bracket/Cover order — parent order se link (SL/Target child orders)
    parent_bracket = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='bracket_children')

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=200, null=True, blank=True)

    def __str__(self):
        return f"{self.side} {self.quantity} {self.stock.symbol} ({self.order_type}) - {self.status}"
    # Site Settings - Admin Panel se editable
class SiteSettings(models.Model):
    site_name = models.CharField(max_length=100, default="Stock Trader")
    contact_email = models.EmailField(default="support@stocktrader.com")
    maintenance_mode = models.BooleanField(default=False)
    maintenance_message = models.TextField(default="We are currently under maintenance. Please check back later.")
    starting_wallet_balance = models.DecimalField(max_digits=12, decimal_places=2, default=100000.00)

    class Meta:
        verbose_name = "Site Setting"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return self.site_name

    def save(self, *args, **kwargs):
        # Ye ensure karta hai ki sirf EK hi SiteSettings row bane (singleton pattern)
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj
    # ---------------------------------------------------------
# 6. Basket — multiple stocks ka group, combined P&L exit rules,
#    total fund cap + per-stock fund cap ke saath
# ---------------------------------------------------------
class Basket(models.Model):
    STATUS_CHOICES = [('OPEN', 'Open'), ('CLOSED', 'Closed')]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=100, default='My Basket')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')

    # Combined exit rules (poore basket ke total P&L par)
    target_pnl = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stop_loss_pnl = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_trailing_sl = models.BooleanField(default=False)
    trailing_distance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    highest_pnl_since_entry = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Fund management
    allocated_fund = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    # Basket ke liye total budget (null = koi limit nahi)
    fund_used = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Ab tak kitna use ho chuka

    total_invested = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.user.username}) - {self.status}"


class BasketAllocation(models.Model):
    """Har stock ke liye is basket ke andar max fund kitna use ho sakta hai."""
    basket = models.ForeignKey(Basket, on_delete=models.CASCADE, related_name='allocations')
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    max_fund = models.DecimalField(max_digits=14, decimal_places=2)
    fund_used = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        unique_together = ('basket', 'stock')

    def __str__(self):
        return f"{self.basket.name} - {self.stock.symbol}: max ₹{self.max_fund}"


class BasketItem(models.Model):
    """Basket ke andar actual execute hui entries."""
    SIDE_CHOICES = [('BUY', 'Buy'), ('SELL', 'Sell')]

    basket = models.ForeignKey(Basket, on_delete=models.CASCADE, related_name='items')
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    quantity = models.PositiveIntegerField()
    entry_price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.side} {self.quantity} {self.stock.symbol} @ ₹{self.entry_price}"