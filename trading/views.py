from django.contrib import messages
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm
from .models import Stock, Wallet, Portfolio, Transaction


# Home page - dikhata hai saare available stocks
@login_required
def dashboard(request):
    stocks = Stock.objects.all()
    wallet, created = Wallet.objects.get_or_create(user=request.user)
    portfolio = Portfolio.objects.filter(user=request.user)

    context = {
        'stocks': stocks,
        'wallet': wallet,
        'portfolio': portfolio,
    }
    return render(request, 'trading/dashboard.html', context)


# Signup page - naya user register karega
def signup_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            Wallet.objects.create(user=user)   # naye user ko wallet milega
            return redirect('dashboard')
    else:
        form = UserCreationForm()
    return render(request, 'trading/signup.html', {'form': form})


# Login page
def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            return render(request, 'trading/login.html', {'error': 'Invalid username or password'})
    return render(request, 'trading/login.html')


# Logout
def logout_view(request):
    logout(request)
    return redirect('login')

# Buy Stock
@login_required
def buy_stock(request, stock_id):
    stock = Stock.objects.get(id=stock_id)
    wallet = Wallet.objects.get(user=request.user)
    quantity = int(request.POST.get('quantity', 1))
    total_cost = stock.current_price * quantity

    if wallet.balance >= total_cost:
        wallet.balance -= total_cost
        wallet.save()

        portfolio, created = Portfolio.objects.get_or_create(
            user=request.user,
            stock=stock,
            defaults={'quantity': 0, 'average_buy_price': stock.current_price}
        )
        total_old_value = portfolio.quantity * portfolio.average_buy_price
        total_new_value = total_old_value + total_cost
        portfolio.quantity += quantity
        portfolio.average_buy_price = total_new_value / portfolio.quantity
        portfolio.save()

        Transaction.objects.create(
            user=request.user,
            stock=stock,
            transaction_type='BUY',
            quantity=quantity,
            price=stock.current_price
        )
        messages.success(request, f"Bought {quantity} shares of {stock.symbol} successfully!")
    else:
        messages.error(request, "Insufficient wallet balance!")

    return redirect('dashboard')
# Sell Stock (Normal Sell + Short Sell)
@login_required
def sell_stock(request, stock_id):
    stock = Stock.objects.get(id=stock_id)
    wallet = Wallet.objects.get(user=request.user)
    quantity = int(request.POST.get('quantity', 1))
    total_value = stock.current_price * quantity

    # Portfolio nikaalo, agar exist nahi karta to naya banao (quantity 0 se)
    portfolio, created = Portfolio.objects.get_or_create(
        user=request.user,
        stock=stock,
        defaults={'quantity': 0, 'average_buy_price': stock.current_price}
    )

    if portfolio.quantity > 0:
        # CASE 1: User ke paas stock hai -> Normal Sell
        if quantity > portfolio.quantity:
            messages.error(request, f"You only have {portfolio.quantity} shares to sell!")
            return redirect('dashboard')

        wallet.balance += total_value
        portfolio.quantity -= quantity
        # Agar sab bech diya to average price reset kar do
        if portfolio.quantity == 0:
            portfolio.average_buy_price = 0

    else:
        # CASE 2: User ke paas stock nahi hai (ya already short hai) -> Short Sell
        wallet.balance += total_value
        total_old_value = abs(portfolio.quantity) * portfolio.average_buy_price
        total_new_value = total_old_value + total_value
        new_quantity = portfolio.quantity - quantity
        portfolio.average_buy_price = total_new_value / abs(new_quantity)
        portfolio.quantity = new_quantity

    wallet.save()
    portfolio.save()

    Transaction.objects.create(
        user=request.user,
        stock=stock,
        transaction_type='SELL',
        quantity=quantity,
        price=stock.current_price
    )
    messages.success(request, f"Sold {quantity} shares of {stock.symbol} successfully!")
    return redirect('dashboard')
# Transaction History Page
@login_required
def transaction_history(request):
    transactions = Transaction.objects.filter(user=request.user).order_by('-timestamp')
    return render(request, 'trading/history.html', {'transactions': transactions})