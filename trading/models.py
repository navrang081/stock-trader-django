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
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=100000.00)  # start with ₹1,00,000

    def __str__(self):
        return f"{self.user.username}'s Wallet - ₹{self.balance}"


# 3. Portfolio — user ke paas kaunsa stock, kitni quantity
class Portfolio(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    average_buy_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('user', 'stock')   # ek user ka ek stock ek hi row me rahega

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