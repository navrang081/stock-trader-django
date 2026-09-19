from django.contrib import messages
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone
from datetime import time as dt_time, timedelta
from zoneinfo import ZoneInfo
from .models import Stock, Wallet, Portfolio, Transaction, Order, Basket, BasketAllocation, BasketItem
# ---------------------------------------------------------
# Helper: SL/Target ke type (POINT/PRICE/PNL) ko absolute
# price me convert karta hai, order place karte waqt
# ---------------------------------------------------------
def calculate_sl_target(order_side, avg_price, quantity, sl_type, sl_value, target_type, target_value):
    avg_price = float(avg_price)
    stop_loss_price = None
    sl_trail_distance = None
    target_price = None

    if sl_type and sl_value not in (None, ''):
        sl_value = float(sl_value)
        if sl_type == 'POINT':
            sl_trail_distance = sl_value
            stop_loss_price = avg_price - sl_value if order_side == 'LONG' else avg_price + sl_value
        elif sl_type == 'PRICE':
            stop_loss_price = sl_value
            sl_trail_distance = abs(avg_price - sl_value)
        elif sl_type == 'PNL':
            if quantity:
                price_diff = sl_value / abs(quantity)
                stop_loss_price = avg_price - price_diff if order_side == 'LONG' else avg_price + price_diff
            sl_trail_distance = sl_value

    if target_type and target_value not in (None, ''):
        target_value = float(target_value)
        if target_type == 'POINT':
            target_price = avg_price + target_value if order_side == 'LONG' else avg_price - target_value
        elif target_type == 'PRICE':
            target_price = target_value
        elif target_type == 'PNL':
            if quantity:
                price_diff = target_value / abs(quantity)
                target_price = avg_price + price_diff if order_side == 'LONG' else avg_price - price_diff

    return stop_loss_price, sl_trail_distance, target_price


# ---------------------------------------------------------
# Helper: Trailing SL ko current price ke hisab se update karta hai
# ---------------------------------------------------------
def update_trailing_sl(item, current_price):
    if not item.is_trailing_sl or not item.stop_loss_type or item.sl_trail_distance is None:
        return

    distance = float(item.sl_trail_distance)

    if item.quantity > 0:  # LONG
        if item.stop_loss_type in ('POINT', 'PRICE'):
            if item.highest_price_since_entry is None or current_price > item.highest_price_since_entry:
                item.highest_price_since_entry = current_price
                new_sl = float(current_price) - distance
                # NEW: Breakeven lock — ek baar trade profit me chala jaye
                # (current price > avg buy price), to SL kabhi breakeven
                # se neeche nahi jayega, chahe trailing distance bada ho
                if float(current_price) > float(item.average_buy_price) and new_sl < float(item.average_buy_price):
                    new_sl = float(item.average_buy_price)
                if item.stop_loss_price is None or new_sl > float(item.stop_loss_price):
                    item.stop_loss_price = new_sl
        elif item.stop_loss_type == 'PNL':
            current_pnl = (float(current_price) - float(item.average_buy_price)) * item.quantity
            if item.highest_pnl_since_entry is None or current_pnl > item.highest_pnl_since_entry:
                item.highest_pnl_since_entry = current_pnl
                trigger_pnl = item.highest_pnl_since_entry - distance
                # NEW: Breakeven lock — ek baar P&L profit me chala jaye,
                # to trigger P&L kabhi 0 (breakeven) se neeche nahi jayega
                if item.highest_pnl_since_entry > 0 and trigger_pnl < 0:
                    trigger_pnl = 0
                price_diff = trigger_pnl / item.quantity
                new_sl = float(item.average_buy_price) + price_diff
                if item.stop_loss_price is None or new_sl > float(item.stop_loss_price):
                    item.stop_loss_price = new_sl

    elif item.quantity < 0:  # SHORT
        short_qty = abs(item.quantity)
        if item.stop_loss_type in ('POINT', 'PRICE'):
            if item.lowest_price_since_entry is None or current_price < item.lowest_price_since_entry:
                item.lowest_price_since_entry = current_price
                new_sl = float(current_price) + distance
                # NEW: Breakeven lock — short profit me hai (price < avg)
                # to SL kabhi breakeven se upar (unfavorable) nahi jayega
                if float(current_price) < float(item.average_buy_price) and new_sl > float(item.average_buy_price):
                    new_sl = float(item.average_buy_price)
                if item.stop_loss_price is None or new_sl < float(item.stop_loss_price):
                    item.stop_loss_price = new_sl
        elif item.stop_loss_type == 'PNL':
            current_pnl = (float(item.average_buy_price) - float(current_price)) * short_qty
            if item.highest_pnl_since_entry is None or current_pnl > item.highest_pnl_since_entry:
                item.highest_pnl_since_entry = current_pnl
                trigger_pnl = item.highest_pnl_since_entry - distance
                # NEW: Breakeven lock — same as LONG PNL, 0 se neeche nahi jayega
                if item.highest_pnl_since_entry > 0 and trigger_pnl < 0:
                    trigger_pnl = 0
                price_diff = trigger_pnl / short_qty
                new_sl = float(item.average_buy_price) - price_diff
                if item.stop_loss_price is None or new_sl < float(item.stop_loss_price):
                    item.stop_loss_price = new_sl

    item.save()


# ---------------------------------------------------------
# Helper: Position close hone par SL/Target fields reset karta hai
# ---------------------------------------------------------
def reset_sl_target_fields(item):
    item.quantity = 0
    item.average_buy_price = 0
    item.stop_loss_price = None
    item.stop_loss_type = None
    item.stop_loss_value = None
    item.is_trailing_sl = False
    item.sl_trail_distance = None
    item.highest_price_since_entry = None
    item.lowest_price_since_entry = None
    item.highest_pnl_since_entry = None
    item.target_price = None
    item.target_type = None
    item.target_value = None
    # ---------------------------------------------------------
# Helper: Basket ka combined P&L nikalna aur exit rules check karna
# ---------------------------------------------------------
def calculate_basket_pnl(basket):
    """Basket ke sabhi items ka combined current P&L nikalta hai."""
    total_pnl = 0
    items = BasketItem.objects.filter(basket=basket)
    for item in items:
        current_price = item.stock.current_price
        if item.side == 'BUY':
            pnl = (float(current_price) - float(item.entry_price)) * item.quantity
        else:  # SELL (short)
            pnl = (float(item.entry_price) - float(current_price)) * item.quantity
        total_pnl += pnl
    return total_pnl


def square_off_basket(request, basket, reason="Auto square-off"):
    """Basket ke sabhi open items ko ek saath square-off (close) karta hai."""
    wallet = Wallet.objects.get(user=basket.user)
    items = BasketItem.objects.filter(basket=basket)

    total_pnl = 0
    for item in items:
        stock = item.stock
        current_price = stock.current_price
        portfolio = Portfolio.objects.filter(user=basket.user, stock=stock).first()

        if not portfolio or portfolio.quantity == 0:
            continue   # pehle se hi kisi aur tarike se close ho chuka

        # Item jitni quantity thi, utni waapis square-off karo (opposite side se)
        qty_to_close = item.quantity
        order_value = float(current_price) * qty_to_close

        if item.side == 'BUY':
            # Buy kiya tha, ab Sell karke close karenge
            wallet.balance = float(wallet.balance) + order_value
            portfolio.quantity -= qty_to_close
            pnl = (float(current_price) - float(item.entry_price)) * qty_to_close
            Transaction.objects.create(
                user=basket.user, stock=stock, transaction_type='SELL',
                quantity=qty_to_close, price=current_price
            )
            
        else:
            # Sell (short) kiya tha, ab Buy karke cover karenge
            wallet.balance = float(wallet.balance) - order_value
            portfolio.quantity += qty_to_close
            pnl = (float(item.entry_price) - float(current_price)) * qty_to_close
            Transaction.objects.create(
                user=basket.user, stock=stock, transaction_type='BUY',
                quantity=qty_to_close, price=current_price
            )

        if portfolio.quantity == 0:
            reset_sl_target_fields(portfolio)
        portfolio.save()
        total_pnl += pnl

    wallet.save()
    basket.status = 'CLOSED'
    from django.utils import timezone
    basket.closed_at = timezone.now()
    basket.save()

    messages.success(request, f"🧺 Basket '{basket.name}' squared off ({reason}). Total P&L: ₹{total_pnl:.2f}")


def check_basket_exit_rules(request, basket):
    """Dashboard load hone par har OPEN basket ka P&L check karke
    Target/SL/Trailing SL match hone par auto square-off karta hai."""
    current_pnl = calculate_basket_pnl(basket)

    # Trailing SL update
    if basket.is_trailing_sl and basket.trailing_distance:
        distance = float(basket.trailing_distance)
        if basket.highest_pnl_since_entry is None or current_pnl > float(basket.highest_pnl_since_entry):
            basket.highest_pnl_since_entry = current_pnl
            new_sl = basket.highest_pnl_since_entry - distance
            if basket.stop_loss_pnl is None or new_sl > float(basket.stop_loss_pnl):
                basket.stop_loss_pnl = new_sl
            basket.save()

    # Target hit?
    if basket.target_pnl is not None and current_pnl >= float(basket.target_pnl):
        square_off_basket(request, basket, reason=f"🎯 Target Achieved (P&L: ₹{current_pnl:.2f})")
        return

    # Stop-Loss hit?
    if basket.stop_loss_pnl is not None and current_pnl <= float(basket.stop_loss_pnl):
        square_off_basket(request, basket, reason=f"🛑 Stop-Loss Triggered (P&L: ₹{current_pnl:.2f})")
        return
# ---------------------------------------------------------
# Helper: Daily loss limit check + reset (common for buy/sell/short)
# ---------------------------------------------------------
def check_daily_loss_halt(wallet):
    """Naya din ho to reset karo, aur agar aaj ka loss limit cross ho gaya
    to True return karega (matlab trading halt)."""
    from datetime import date
    if wallet.last_reset_date != date.today():
        wallet.todays_loss = 0
        wallet.last_reset_date = date.today()
        wallet.is_trading_halted = False
        wallet.save()

    if wallet.todays_loss >= wallet.daily_loss_limit:
        if not wallet.is_trading_halted:
            wallet.is_trading_halted = True
            wallet.save()
        return True
    return False


# ---------------------------------------------------------
# Helper: Short selling ke liye margin block karna
# ---------------------------------------------------------
def required_margin_for_short(current_price, quantity, wallet):
    """Short position ki value ka X% margin chahiye hota hai."""
    position_value = float(current_price) * quantity
    return position_value * (float(wallet.short_margin_percent) / 100)


def available_margin(wallet):
    """Wallet ka free (unblocked) balance."""
    return float(wallet.balance) - float(wallet.margin_used)


# ---------------------------------------------------------
# Helper: Max open positions check
# ---------------------------------------------------------
def count_open_positions(user, exclude_stock_id=None):
    qs = Portfolio.objects.filter(user=user).exclude(quantity=0)
    if exclude_stock_id:
        qs = qs.exclude(stock_id=exclude_stock_id)
    return qs.count()


# ---------------------------------------------------------
# NEW Helper: Market abhi open hai ya nahi (AMO ke liye)
# Market hours: 9:15 AM - 3:30 PM IST, Mon-Fri
# ---------------------------------------------------------
MARKET_OPEN_TIME = dt_time(9, 15)
MARKET_CLOSE_TIME = dt_time(15, 30)

def is_market_open():
    """IST ke hisab se check karta hai ki market abhi open hai ya nahi.
    Weekend (Sat/Sun) par market band maana jata hai."""
    now_ist = timezone.now().astimezone(ZoneInfo("Asia/Kolkata"))
    if now_ist.weekday() >= 5:   # Saturday=5, Sunday=6 -> market band
        return False
    current_time = now_ist.time()
    return MARKET_OPEN_TIME <= current_time <= MARKET_CLOSE_TIME


# ---------------------------------------------------------
# NEW Helper: AMO order ke liye agla trading day nikalta hai
# (Saturday/Sunday skip karke) — AMO ki auto-expiry ke liye use hota hai
# ---------------------------------------------------------
def get_next_trading_day(from_date):
    next_day = from_date + timedelta(days=1)
    while next_day.weekday() >= 5:   # Saturday=5, Sunday=6 -> skip
        next_day += timedelta(days=1)
    return next_day


# Home page -DESHBORD - dikhata hai saare available stocks
@login_required
def dashboard(request):
    from datetime import date

    stocks = Stock.objects.all()
    wallet, created = Wallet.objects.get_or_create(user=request.user)

    # Naya din shuru hua? Todays_loss reset karo
    if wallet.last_reset_date != date.today():
        wallet.todays_loss = 0
        wallet.last_reset_date = date.today()
        wallet.save()

    # ---------------------------------------------------------
    # PENDING LIMIT ORDERS CHECK (price match hote hi auto-execute)
    # ---------------------------------------------------------
    pending_orders = Order.objects.filter(user=request.user, status='PENDING', order_type__in=['MARKET', 'LIMIT', 'SL-M', 'SL-L'])
    for order in pending_orders:
        current_price = order.stock.current_price
        stock = order.stock

        # ---------------------------------------------------------
        # NEW: GTT Expiry check — agar GTT order ki expiry date nikal
        # chuki hai, to use EXPIRED kar do, execute karne ki koshish mat karo
        # ---------------------------------------------------------
        if order.validity == 'GTT' and order.expiry_date:
            from datetime import date
            if order.expiry_date < date.today():
                order.status = 'EXPIRED'
                order.save()
                messages.warning(request, f"⌛ GTT Order Expired: {order.side} {order.quantity} {stock.symbol} (Expiry: {order.expiry_date})")
                continue   # is order ko skip karo, aage execute check mat karo

        # ---------------------------------------------------------
        # NEW: AMO Expiry check — agar AMO order apne valid trading-day
        # (jis din tak ke liye queued tha) tak execute nahi hua,
        # to use EXPIRED kar do, hamesha ke liye PENDING mat rehne do
        # ---------------------------------------------------------
        if order.validity == 'AMO' and order.expiry_date:
            if date.today() > order.expiry_date:
                order.status = 'EXPIRED'
                order.save()
                messages.warning(request, f"⌛ AMO Order Expired: {order.side} {order.quantity} {stock.symbol} (Valid till: {order.expiry_date})")
                continue   # is order ko skip karo, aage execute check mat karo
                    # ---------------------------------------------------------
        # NEW: SCHEDULED time-gate — tay kiya hua time aane tak
        # is order ko bilkul touch mat karo, chahe price condition
        # match kyu na ho rahi ho
        # ---------------------------------------------------------
        if order.validity == 'SCHEDULED' and order.execute_after:
            if timezone.now() < order.execute_after:
                continue   # abhi time nahi aaya, skip karo

        # ---------------------------------------------------------
        # NEW: AMO gate — market band hai to is order ko bilkul touch
        # mat karo, price condition check bhi mat karo. Market khulte
        # hi agli dashboard load par ye normal price-matching logic
        # (MARKET/LIMIT/SL-M/SL-L) se hi execute hoga.
        # ---------------------------------------------------------
        if order.validity == 'AMO' and not is_market_open():
            continue   # market abhi band hai, agli baar dashboard load par phir check hoga

        should_execute = False
        execution_price = current_price   # default: market price par execute hoga

        if order.order_type == 'LIMIT':
            if order.side == 'BUY' and current_price <= order.limit_price:
                should_execute = True
            elif order.side == 'SELL' and current_price >= order.limit_price:
                should_execute = True

        elif order.order_type == 'SL-M':
            # Trigger hit hote hi market price par turant execute
            if order.side == 'BUY' and current_price >= order.trigger_price:
                should_execute = True
            elif order.side == 'SELL' and current_price <= order.trigger_price:
                should_execute = True

        elif order.order_type == 'SL-L':
            # Pehle trigger hit hona chahiye, phir limit price ki shart bhi puri honi chahiye
            trigger_hit = False
            if order.side == 'BUY' and current_price >= order.trigger_price:
                trigger_hit = True
            elif order.side == 'SELL' and current_price <= order.trigger_price:
                trigger_hit = True

            if trigger_hit:
                if order.side == 'BUY' and current_price <= order.limit_price:
                    should_execute = True
                elif order.side == 'SELL' and current_price >= order.limit_price:
                    should_execute = True
                # Trigger hit hua par limit price abhi favorable nahi — pending hi rahega,
                # agli baar dashboard load hone par phir check hoga.
        elif order.order_type == 'MARKET':
            # Scheduled+Market: time-gate paar ho chuka hai (upar check ho chuka),
            # ab seedha execute karo, koi price condition nahi chahiye
            should_execute = True

        if not should_execute:
            continue

        total_value = current_price * order.quantity
        port, created = Portfolio.objects.get_or_create(
            user=request.user, stock=stock,
            defaults={'quantity': 0, 'average_buy_price': current_price}
        )

        if order.side == 'BUY':
            if wallet.balance < total_value:
                order.status = 'REJECTED'
                order.reject_reason = 'Insufficient balance at execution time'
                order.save()
                continue

            wallet.balance -= total_value
            total_old_value = port.quantity * port.average_buy_price
            total_new_value = total_old_value + total_value
            port.quantity += order.quantity
            if port.quantity != 0:
                port.average_buy_price = total_new_value / port.quantity
            else:
                port.average_buy_price = 0
            port.save()

            Transaction.objects.create(
                user=request.user, stock=stock, transaction_type='BUY',
                quantity=order.quantity, price=current_price
            )
        else:
            wallet.balance += total_value
            if port.quantity > 0:
                port.quantity -= order.quantity
                if port.quantity == 0:
                    reset_sl_target_fields(port)
            else:
                total_old_value = abs(port.quantity) * port.average_buy_price
                total_new_value = total_old_value + total_value
                new_quantity = port.quantity - order.quantity
                port.average_buy_price = total_new_value / abs(new_quantity) if new_quantity != 0 else 0
                port.quantity = new_quantity
            port.save()

            Transaction.objects.create(
                user=request.user, stock=stock, transaction_type='SELL',
                quantity=order.quantity, price=current_price
            )

        wallet.save()
        order.status = 'EXECUTED'
        order.executed_at = timezone.now()
        order.save()
        messages.success(request, f"✅ Limit Order Executed: {order.side} {order.quantity} {stock.symbol} @ ₹{current_price}")

    portfolio = Portfolio.objects.filter(user=request.user)

    # AUTO STOP-LOSS / TARGET CHECK (trailing update ke saath)
    for item in portfolio:
        current_price = item.stock.current_price

        # Trailing SL ko pehle refresh karo
        update_trailing_sl(item, current_price)

        if item.quantity > 0:  # LONG position
            if item.stop_loss_price and current_price <= item.stop_loss_price:
                total_value = current_price * item.quantity
                profit_loss = (current_price - item.average_buy_price) * item.quantity
                if profit_loss < 0:
                    wallet.todays_loss += abs(profit_loss)
                wallet.balance += total_value
                Transaction.objects.create(
                    user=request.user, stock=item.stock, transaction_type='SELL',
                    quantity=item.quantity, price=current_price
                )
                messages.warning(request, f"🛑 Trailing Stop Loss Triggered! Sold {item.quantity} {item.stock.symbol} @ ₹{current_price}")
                reset_sl_target_fields(item)
                wallet.save()
                item.save()

            elif item.target_price and current_price >= item.target_price:
                total_value = current_price * item.quantity
                profit_loss = (current_price - item.average_buy_price) * item.quantity
                wallet.balance += total_value
                Transaction.objects.create(
                    user=request.user, stock=item.stock, transaction_type='SELL',
                    quantity=item.quantity, price=current_price
                )
                messages.success(request, f"🎯 Target Achieved! Sold {item.quantity} {item.stock.symbol} @ ₹{current_price} (Profit: ₹{profit_loss:.2f})")
                reset_sl_target_fields(item)
                wallet.save()
                item.save()

        elif item.quantity < 0:  # SHORT position
            short_qty = abs(item.quantity)

            if item.stop_loss_price and current_price >= item.stop_loss_price:
                total_cost = current_price * short_qty
                profit_loss = (item.average_buy_price - current_price) * short_qty
                if profit_loss < 0:
                    wallet.todays_loss += abs(profit_loss)
                wallet.balance -= total_cost
                Transaction.objects.create(
                    user=request.user, stock=item.stock, transaction_type='BUY',
                    quantity=short_qty, price=current_price
                )
                messages.warning(request, f"🛑 Trailing Stop Loss Triggered (Short)! Bought back {short_qty} {item.stock.symbol} @ ₹{current_price}")
                reset_sl_target_fields(item)
                wallet.save()
                item.save()

            elif item.target_price and current_price <= item.target_price:
                total_cost = current_price * short_qty
                profit_loss = (item.average_buy_price - current_price) * short_qty
                wallet.balance -= total_cost
                Transaction.objects.create(
                    user=request.user, stock=item.stock, transaction_type='BUY',
                    quantity=short_qty, price=current_price
                )
                messages.success(request, f"🎯 Target Achieved (Short)! Bought back {short_qty} {item.stock.symbol} @ ₹{current_price} (Profit: ₹{profit_loss:.2f})")
                reset_sl_target_fields(item)
                wallet.save()
                item.save()

    portfolio = Portfolio.objects.filter(user=request.user)  # Refresh after auto-sell

    my_pending_orders = Order.objects.filter(user=request.user, status='PENDING').order_by('-created_at')
  
    my_baskets = Basket.objects.filter(user=request.user, status='OPEN').prefetch_related('allocations', 'items')

    # 🔧 NEW: har open basket ka target/SL/trailing check karo (auto square-off trigger karega)
    for b in my_baskets:
        check_basket_exit_rules(request, b)

    # 🔧 NEW: refresh karo — kyunki upar wale loop me kuch baskets CLOSED ho sakte hain
    my_baskets = Basket.objects.filter(user=request.user, status='OPEN').prefetch_related('allocations', 'items')

    # Har basket ka live combined P&L bhi bhej rahe hai template ko dikhane ke liye
    basket_data = []
    for b in my_baskets:
        basket_data.append({
            'basket': b,
            'pnl': calculate_basket_pnl(b),
            'allocations': b.allocations.all(),
        })

    context = {
        'stocks': stocks,
        'wallet': wallet,
        'portfolio': portfolio,
        'pending_orders': my_pending_orders,
        'basket_data': basket_data,
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
# Buy Stock
@login_required
def buy_stock(request, stock_id):
    stock = Stock.objects.get(id=stock_id)
    wallet = Wallet.objects.get(user=request.user)
    quantity = int(request.POST.get('quantity', 1))

    sl_type = request.POST.get('sl_type')
    sl_value = request.POST.get('sl_value')
    is_trailing = request.POST.get('is_trailing_sl') == 'on'
    target_type = request.POST.get('target_type')
    target_value = request.POST.get('target_value')

    total_cost = stock.current_price * quantity

    # RISK CHECK 0: Trading halted? (NEW — common check)
    if check_daily_loss_halt(wallet):
        messages.error(request, f"🚫 Daily loss limit (₹{wallet.daily_loss_limit}) reached! Trading halted for today.")
        return redirect('dashboard')

    # RISK CHECK 1: Wallet balance
    if wallet.balance < total_cost:
        messages.error(request, "Insufficient wallet balance!")
        return redirect('dashboard')

    # RISK CHECK 2: Position size limit
    max_allowed = (wallet.balance + total_cost) * (wallet.max_position_percent / 100)
    if total_cost > max_allowed:
        messages.error(request, f"Position too large! Max allowed per stock: ₹{max_allowed:.2f} ({wallet.max_position_percent}% of wallet)")
        return redirect('dashboard')

    # RISK CHECK 3: Max open positions (NEW)
    existing_position = Portfolio.objects.filter(user=request.user, stock=stock).first()
    is_new_position = not existing_position or existing_position.quantity == 0
    if is_new_position and count_open_positions(request.user) >= wallet.max_open_positions:
        messages.error(request, f"🚫 Max open positions limit ({wallet.max_open_positions}) reached! Close a position before opening a new one.")
        return redirect('dashboard')

    # ... baaki buy logic same rahega ...
    # Sab checks pass — ab buy karo
    wallet.balance -= total_cost
    wallet.save()
    portfolio, created = Portfolio.objects.get_or_create(
        user=request.user,
        stock=stock,
        defaults={'quantity': 0, 'average_buy_price': stock.current_price}
    )

    was_short = portfolio.quantity < 0  # Kya ye short position thi?

    total_old_value = portfolio.quantity * portfolio.average_buy_price
    total_new_value = total_old_value + total_cost
    portfolio.quantity += quantity
    if portfolio.quantity != 0:
        portfolio.average_buy_price = total_new_value / portfolio.quantity
    else:
        portfolio.average_buy_price = 0

    # Agar short position poori tarah cover ho gayi (0 pe aa gayi), SL/Target reset karo
    if was_short and portfolio.quantity >= 0:
        remaining_qty = portfolio.quantity
        remaining_avg = total_new_value / remaining_qty if remaining_qty else 0
        reset_sl_target_fields(portfolio)
        portfolio.quantity = remaining_qty
        portfolio.average_buy_price = remaining_avg

    # Stop Loss aur Target save karo (agar diya ho, aur ye ek NAYI/existing Long position hai)
    if not was_short and (sl_type or target_type):
        sl_price, trail_dist, tgt_price = calculate_sl_target(
            'LONG', portfolio.average_buy_price, portfolio.quantity,
            sl_type, sl_value, target_type, target_value
        )
        if sl_type:
            portfolio.stop_loss_type = sl_type
            portfolio.stop_loss_value = float(sl_value) if sl_value else None
            portfolio.stop_loss_price = sl_price
            portfolio.is_trailing_sl = is_trailing
            portfolio.sl_trail_distance = trail_dist
            portfolio.highest_price_since_entry = stock.current_price
            portfolio.highest_pnl_since_entry = None
        if target_type:
            portfolio.target_type = target_type
            portfolio.target_value = float(target_value) if target_value else None
            portfolio.target_price = tgt_price

    portfolio.save()

    Transaction.objects.create(
        user=request.user,
        stock=stock,
        transaction_type='BUY',
        quantity=quantity,
        price=stock.current_price
    )
    messages.success(request, f"Bought {quantity} shares of {stock.symbol} successfully!")
    return redirect('dashboard')
# Sell Stock (Normal Sell + Short Sell)
# Sell Stock (Normal Sell + Short Sell)
@login_required
def sell_stock(request, stock_id):
    stock = Stock.objects.get(id=stock_id)
    wallet = Wallet.objects.get(user=request.user)
    quantity = int(request.POST.get('quantity', 1))

    sl_type = request.POST.get('sl_type')
    sl_value = request.POST.get('sl_value')
    is_trailing = request.POST.get('is_trailing_sl') == 'on'
    target_type = request.POST.get('target_type')
    target_value = request.POST.get('target_value')

    total_value = stock.current_price * quantity

    # RISK CHECK 0: Trading halted?
    if check_daily_loss_halt(wallet):
        messages.error(request, f"🚫 Daily loss limit (₹{wallet.daily_loss_limit}) reached! Trading halted for today.")
        return redirect('dashboard')
    portfolio, created = Portfolio.objects.get_or_create(
        user=request.user,
        stock=stock,
        defaults={'quantity': 0, 'average_buy_price': stock.current_price}
    )

    if portfolio.quantity > 0:
        if quantity > portfolio.quantity:
            messages.error(request, f"You only have {portfolio.quantity} shares to sell!")
            return redirect('dashboard')

        profit_loss = (stock.current_price - portfolio.average_buy_price) * quantity
        if profit_loss < 0:
            wallet.todays_loss += abs(profit_loss)

        wallet.balance += total_value
        portfolio.quantity -= quantity
        if portfolio.quantity == 0:
            reset_sl_target_fields(portfolio)

    else:
        # Short selling (naya short ya already short me aur add karna)

        # RISK CHECK: Margin available hai kya?
        needed_margin = required_margin_for_short(stock.current_price, quantity, wallet)
        if available_margin(wallet) < needed_margin:
            messages.error(request, f"🚫 Insufficient margin for short sell! Need ₹{needed_margin:.2f}, available ₹{available_margin(wallet):.2f}")
            return redirect('dashboard')

        # RISK CHECK: Position size limit
        max_allowed = wallet.balance * (wallet.max_position_percent / 100)
        if total_value > max_allowed:
            messages.error(request, f"Short position too large! Max allowed: ₹{max_allowed:.2f} ({wallet.max_position_percent}% of wallet)")
            return redirect('dashboard')

        # RISK CHECK: Max open positions
        if portfolio.quantity == 0 and count_open_positions(request.user) >= wallet.max_open_positions:
            messages.error(request, f"🚫 Max open positions limit ({wallet.max_open_positions}) reached!")
            return redirect('dashboard')

        # Margin block karo
        wallet.margin_used = float(wallet.margin_used) + needed_margin

        wallet.balance += total_value
        total_old_value = abs(portfolio.quantity) * portfolio.average_buy_price
        total_new_value = total_old_value + total_value
        new_quantity = portfolio.quantity - quantity
        portfolio.average_buy_price = total_new_value / abs(new_quantity)
        portfolio.quantity = new_quantity

        # Short position ke liye Stop Loss / Target save karo (agar diya ho)
        if sl_type or target_type:
            sl_price, trail_dist, tgt_price = calculate_sl_target(
                'SHORT', portfolio.average_buy_price, portfolio.quantity,
                sl_type, sl_value, target_type, target_value
            )
            if sl_type:
                portfolio.stop_loss_type = sl_type
                portfolio.stop_loss_value = float(sl_value) if sl_value else None
                portfolio.stop_loss_price = sl_price
                portfolio.is_trailing_sl = is_trailing
                portfolio.sl_trail_distance = trail_dist
                portfolio.lowest_price_since_entry = stock.current_price
                portfolio.highest_pnl_since_entry = None
            if target_type:
                portfolio.target_type = target_type
                portfolio.target_value = float(target_value) if target_value else None
                portfolio.target_price = tgt_price

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
# ---------------------------------------------------------
# Limit Order place karna (Pending order create hota hai)
# ---------------------------------------------------------
# Limit Order place karna (Pending order create hota hai)
# ---------------------------------------------------------
@login_required
def place_order(request, stock_id):
    stock = Stock.objects.get(id=stock_id)
    side = request.POST.get('side')
    quantity = int(request.POST.get('quantity', 1))
    order_type = request.POST.get('order_type')          # MARKET / LIMIT / SL-M / SL-L
    product_type = request.POST.get('product_type', 'NORMAL')   # INTRADAY / NORMAL
    validity = request.POST.get('validity', 'DAY')        # DAY / IOC / AMO / GTT
    limit_price = request.POST.get('limit_price')
    trigger_price = request.POST.get('trigger_price')
    slippage_percent = request.POST.get('slippage_percent')
    expiry_date = request.POST.get('expiry_date')  # GTT ke liye
    execute_after = request.POST.get('execute_after')  # Scheduled order ke liye
    execute_after_dt = None
    if execute_after:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        naive_dt = datetime.strptime(execute_after, '%Y-%m-%dT%H:%M')
        ist = ZoneInfo("Asia/Kolkata")
        execute_after_dt = naive_dt.replace(tzinfo=ist)   # Seedha IST bata do, settings.TIME_ZONE par depend mat karo
    current_price = stock.current_price

    # ---- Validation: order type ke hisab se zaroori fields check karo ----
    if order_type == 'LIMIT' and not limit_price:
        messages.error(request, "Limit price is required for Limit order!")
        return redirect('dashboard')

    if order_type in ('SL-M', 'SL-L') and not trigger_price:
        messages.error(request, "Trigger price is required for Stop-Loss order!")
        return redirect('dashboard')

    if order_type == 'SL-L' and not limit_price:
        messages.error(request, "Limit price is required for SL-L order!")
        return redirect('dashboard')

    if validity == 'GTT' and not expiry_date:
        messages.error(request, "Expiry date is required for GTT order!")
        return redirect('dashboard')

    # ---------------------------------------------------------
    # NEW: AMO — sirf market band hone par hi place karne do,
    # market open hai to AMO ka koi matlab nahi, normal order use karo
    # ---------------------------------------------------------
    if validity == 'AMO' and is_market_open():
        messages.error(request, "🕒 Market abhi open hai! AMO sirf market band hone ke baad place kiya ja sakta hai.")
        return redirect('dashboard')

    if validity == 'SCHEDULED' and not execute_after:
        messages.error(request, "Execute At date & time is required for Scheduled order!")
        return redirect('dashboard')
        # ---------------------------------------------------------
    # NEW: SCHEDULED validity — chahe order_type Market/Limit/SL-M/SL-L
    # kuch bhi ho, turant execute NAHI karna. Seedha PENDING bana do,
    # asli condition check tab hoga jab tay kiya hua time aa jayega.
    # ---------------------------------------------------------
    if validity == 'SCHEDULED':
        order = Order.objects.create(
            user=request.user,
            stock=stock,
            order_type=order_type,
            side=side,
            quantity=quantity,
            limit_price=float(limit_price) if limit_price else None,
            trigger_price=float(trigger_price) if trigger_price else None,
            product_type=product_type,
            validity=validity,
            slippage_percent=float(slippage_percent) if slippage_percent else None,
            reference_price=current_price,
            execute_after=execute_after_dt,  
            status='PENDING'
        )
        messages.success(request, f"⏰ Scheduled Order placed: {side} {quantity} {stock.symbol} ({order.get_order_type_display()}) — will activate at {execute_after}")
        return redirect('dashboard')

    # ---- MARKET order: agar validity DAY hai, turant try karo ----
    

    # ---- MARKET order: agar validity DAY hai, turant try karo ----
    if order_type == 'MARKET' and validity == 'DAY':
        slip = float(slippage_percent) if slippage_percent else None
        if slip:
            max_price = float(current_price) * (1 + slip / 100)
            min_price = float(current_price) * (1 - slip / 100)

        # Market order turant execute — buy_stock/sell_stock jaisa direct logic
        return execute_market_order(request, stock, side, quantity, product_type)

    # ---------------------------------------------------------
    # IOC (Immediate or Cancel) — turant match check karo,
    # match hua to turant execute, warna PENDING mat banao, seedha CANCEL karo
    # ---------------------------------------------------------
    if validity == 'IOC':
        should_execute_now = False
        current_price_f = float(current_price)

        if order_type == 'MARKET':
            should_execute_now = True

        elif order_type == 'LIMIT':
            if side == 'BUY' and current_price_f <= float(limit_price):
                should_execute_now = True
            elif side == 'SELL' and current_price_f >= float(limit_price):
                should_execute_now = True

        elif order_type == 'SL-M':
            if side == 'BUY' and current_price_f >= float(trigger_price):
                should_execute_now = True
            elif side == 'SELL' and current_price_f <= float(trigger_price):
                should_execute_now = True

        elif order_type == 'SL-L':
            trigger_hit = False
            if side == 'BUY' and current_price_f >= float(trigger_price):
                trigger_hit = True
            elif side == 'SELL' and current_price_f <= float(trigger_price):
                trigger_hit = True
            if trigger_hit:
                if side == 'BUY' and current_price_f <= float(limit_price):
                    should_execute_now = True
                elif side == 'SELL' and current_price_f >= float(limit_price):
                    should_execute_now = True

        if should_execute_now:
            # Turant match ho gaya — market order jaisa execute karo
            return execute_market_order(request, stock, side, quantity, product_type)
        else:
            # Match nahi hua — PENDING me nahi rakhna, seedha CANCEL karo
            Order.objects.create(
                user=request.user, stock=stock, order_type=order_type, side=side,
                quantity=quantity,
                limit_price=float(limit_price) if limit_price else None,
                trigger_price=float(trigger_price) if trigger_price else None,
                product_type=product_type,
                validity=validity,
                reference_price=current_price,
                status='CANCELLED',
                reject_reason='IOC: turant match nahi mila'
            )
            messages.warning(request, f"⚡ IOC Order Cancelled: {side} {quantity} {stock.symbol} ka turant match nahi mila.")
            return redirect('dashboard')

    # ---------------------------------------------------------
    # NEW: AMO auto-expiry — agar AMO order aaj execute nahi hota
    # (market band hone ya price match na hone ki wajah se), to
    # agle trading day (Sat/Sun skip karke) ke ant tak valid rahega,
    # uske baad EXPIRED ho jayega. Iske liye Order model ka existing
    # expiry_date field hi reuse ho raha hai — koi naya migration nahi chahiye.
    # ---------------------------------------------------------
    amo_expiry = None
    if validity == 'AMO':
        placed_date = timezone.now().astimezone(ZoneInfo("Asia/Kolkata")).date()
        amo_expiry = get_next_trading_day(placed_date)

    # ---- Baaki sab order types (LIMIT, SL-M, SL-L, ya AMO/GTT validity) PENDING banao ----
    order = Order.objects.create(
        user=request.user,
        stock=stock,
        order_type=order_type,
        side=side,
        quantity=quantity,
        limit_price=float(limit_price) if limit_price else None,
        trigger_price=float(trigger_price) if trigger_price else None,
        product_type=product_type,
        validity=validity,
        slippage_percent=float(slippage_percent) if slippage_percent else None,
        reference_price=current_price,
        expiry_date=amo_expiry if validity == 'AMO' else (expiry_date if expiry_date else None),
        status='PENDING'
    )
    if validity == 'AMO':
        messages.success(request, f"🌙 AMO Order placed: {side} {quantity} {stock.symbol} ({order.get_order_type_display()}) — will activate at next market open, valid till {amo_expiry}")
    else:
        messages.success(request, f"📝 {order.get_order_type_display()} order placed: {side} {quantity} {stock.symbol} ({order.get_validity_display()})")
    return redirect('dashboard')


# ---------------------------------------------------------
# Helper: Market order turant execute karna (Buy/Sell/Short teeno cases)
# ---------------------------------------------------------
def execute_market_order(request, stock, side, quantity, product_type):
    wallet = Wallet.objects.get(user=request.user)

    if check_daily_loss_halt(wallet):
        messages.error(request, f"🚫 Daily loss limit (₹{wallet.daily_loss_limit}) reached! Trading halted for today.")
        return redirect('dashboard')

    if side == 'BUY':
        request.POST = request.POST.copy()
        request.POST['quantity'] = str(quantity)
        return buy_stock(request, stock.id)
    else:
        request.POST = request.POST.copy()
        request.POST['quantity'] = str(quantity)
        return sell_stock(request, stock.id)

# ---------------------------------------------------------
# Pending order cancel karna
# ---------------------------------------------------------
@login_required
def cancel_order(request, order_id):
    order = Order.objects.get(id=order_id, user=request.user)
    if order.status == 'PENDING':
        order.status = 'CANCELLED'
        order.save()
        messages.success(request, f"Order cancelled: {order.side} {order.quantity} {order.stock.symbol}")
    else:
        messages.error(request, "This order can't be cancelled (already executed/cancelled).")
    return redirect('dashboard')
@login_required
def modify_position_sl(request, portfolio_id):
    """
    NEW: Ek already-open (executed) portfolio position ka SL/Target/Trailing SL
    baad me add ya update karna — bina naya buy/sell order kiye.
    Agar sl_type/target_type khali bheja jaye, to us wale field ko clear kar deta hai.
    """
    portfolio = Portfolio.objects.get(id=portfolio_id, user=request.user)

    if portfolio.quantity == 0:
        messages.error(request, "Ye position already closed hai, SL/Target modify nahi ho sakta.")
        return redirect('dashboard')

    sl_type = request.POST.get('sl_type')
    sl_value = request.POST.get('sl_value')
    is_trailing = request.POST.get('is_trailing_sl') == 'on'
    target_type = request.POST.get('target_type')
    target_value = request.POST.get('target_value')

    order_side = 'LONG' if portfolio.quantity > 0 else 'SHORT'
    current_price = portfolio.stock.current_price

    sl_price, trail_dist, tgt_price = calculate_sl_target(
        order_side, portfolio.average_buy_price, portfolio.quantity,
        sl_type, sl_value, target_type, target_value
    )

    if sl_type:
        portfolio.stop_loss_type = sl_type
        portfolio.stop_loss_value = float(sl_value) if sl_value else None
        portfolio.stop_loss_price = sl_price
        portfolio.is_trailing_sl = is_trailing
        portfolio.sl_trail_distance = trail_dist
        # Trailing trackers current price se fresh start karo
        if order_side == 'LONG':
            portfolio.highest_price_since_entry = current_price
            portfolio.lowest_price_since_entry = None
        else:
            portfolio.lowest_price_since_entry = current_price
            portfolio.highest_price_since_entry = None
        portfolio.highest_pnl_since_entry = None
    else:
        # sl_type khali chhoda gaya — matlab SL hataana hai
        portfolio.stop_loss_type = None
        portfolio.stop_loss_value = None
        portfolio.stop_loss_price = None
        portfolio.is_trailing_sl = False
        portfolio.sl_trail_distance = None
        portfolio.highest_price_since_entry = None
        portfolio.lowest_price_since_entry = None
        portfolio.highest_pnl_since_entry = None

    if target_type:
        portfolio.target_type = target_type
        portfolio.target_value = float(target_value) if target_value else None
        portfolio.target_price = tgt_price
    else:
        # target_type khali chhoda gaya — matlab Target hataana hai
        portfolio.target_type = None
        portfolio.target_value = None
        portfolio.target_price = None

    portfolio.save()
    messages.success(request, f"🎯 {portfolio.stock.symbol} ke liye SL/Target update ho gaya!")
    return redirect('dashboard')


# ---------------------------------------------------------
# BASKET ORDER — Create/Edit/Execute
# ---------------------------------------------------------

@login_required
def create_basket(request):
    """Naya basket banana — naam, total fund, aur exit rules (combined P&L par) ke saath."""
    if request.method == 'POST':
        name = request.POST.get('name', 'My Basket')
        allocated_fund = request.POST.get('allocated_fund')
        target_pnl = request.POST.get('target_pnl')
        stop_loss_pnl = request.POST.get('stop_loss_pnl')
        is_trailing = request.POST.get('is_trailing_sl') == 'on'
        trailing_distance = request.POST.get('trailing_distance')

        Basket.objects.create(
            user=request.user,
            name=name,
            allocated_fund=float(allocated_fund) if allocated_fund else None,
            target_pnl=float(target_pnl) if target_pnl else None,
            stop_loss_pnl=float(stop_loss_pnl) if stop_loss_pnl else None,
            is_trailing_sl=is_trailing,
            trailing_distance=float(trailing_distance) if trailing_distance else None,
        )
        messages.success(request, f"🧺 Basket '{name}' created!")
    return redirect('dashboard')


@login_required
def edit_basket(request, basket_id):
    """Basket ka naam, fund, exit rules edit karna."""
    basket = Basket.objects.get(id=basket_id, user=request.user)
    if request.method == 'POST':
        basket.name = request.POST.get('name', basket.name)
        allocated_fund = request.POST.get('allocated_fund')
        target_pnl = request.POST.get('target_pnl')
        stop_loss_pnl = request.POST.get('stop_loss_pnl')
        trailing_distance = request.POST.get('trailing_distance')

        basket.allocated_fund = float(allocated_fund) if allocated_fund else None
        basket.target_pnl = float(target_pnl) if target_pnl else None
        basket.stop_loss_pnl = float(stop_loss_pnl) if stop_loss_pnl else None
        basket.is_trailing_sl = request.POST.get('is_trailing_sl') == 'on'
        basket.trailing_distance = float(trailing_distance) if trailing_distance else None
        basket.save()
        messages.success(request, f"✏️ Basket '{basket.name}' updated!")
    return redirect('dashboard')

@login_required
def update_basket_trailing(request, basket_id):
    """Sirf basket ki Trailing SL settings (on/off + distance) quick-update karna,
    baaki fields (Target, Fund, fixed SL) ko touch kiye bina."""
    basket = Basket.objects.get(id=basket_id, user=request.user)
    if request.method == 'POST':
        is_trailing = request.POST.get('is_trailing_sl') == 'on'
        trailing_distance = request.POST.get('trailing_distance')

        basket.is_trailing_sl = is_trailing
        basket.trailing_distance = float(trailing_distance) if trailing_distance else None

        basket.save()
        messages.success(request, f"🔄 Trailing SL settings updated for '{basket.name}'")
    return redirect('dashboard')
@login_required
def add_stock_to_basket(request, basket_id):
    """Basket ke andar ek stock ke liye max fund allocate karna."""
    basket = Basket.objects.get(id=basket_id, user=request.user)
    stock_id = request.POST.get('stock_id')
    max_fund = request.POST.get('max_fund')
    stock = Stock.objects.get(id=stock_id)

    if not max_fund:
        messages.error(request, "Max fund is required!")
        return redirect('dashboard')

    # Agar basket ka overall fund set hai to check karo naya allocation usse zyada na ho jaye
    if basket.allocated_fund:
        existing_allocations = BasketAllocation.objects.filter(basket=basket).exclude(stock=stock)
        total_other_allocations = sum([float(a.max_fund) for a in existing_allocations])
        if total_other_allocations + float(max_fund) > float(basket.allocated_fund):
            messages.error(request, f"🚫 Total allocation (₹{total_other_allocations + float(max_fund):.2f}) exceeds basket fund limit (₹{basket.allocated_fund})!")
            return redirect('dashboard')

    allocation, created = BasketAllocation.objects.get_or_create(
        basket=basket, stock=stock,
        defaults={'max_fund': float(max_fund)}
    )
    if not created:
        allocation.max_fund = float(max_fund)
        allocation.save()

    messages.success(request, f"➕ {stock.symbol} added to '{basket.name}' with max ₹{max_fund}")
    return redirect('dashboard')


@login_required
def remove_stock_from_basket(request, allocation_id):
    allocation = BasketAllocation.objects.get(id=allocation_id, basket__user=request.user)
    basket_name = allocation.basket.name
    stock_symbol = allocation.stock.symbol
    allocation.delete()
    messages.success(request, f"➖ {stock_symbol} removed from '{basket_name}'")
    return redirect('dashboard')


@login_required
def close_basket(request, basket_id):
    """Poore basket ko manually band karna — sabhi items square-off karo."""
    basket = Basket.objects.get(id=basket_id, user=request.user)
    square_off_basket(request, basket, reason="Manually closed")
    return redirect('dashboard')
@login_required
def execute_basket(request, basket_id):
    """Basket ke selected stocks ko ek saath Market price par execute karna."""
    basket = Basket.objects.get(id=basket_id, user=request.user)
    wallet = Wallet.objects.get(user=request.user)

    if check_daily_loss_halt(wallet):
        messages.error(request, f"🚫 Daily loss limit reached! Trading halted for today.")
        return redirect('dashboard')

    allocations = BasketAllocation.objects.filter(basket=basket)
    if not allocations.exists():
        messages.error(request, "Basket me koi stock allocate nahi hai!")
        return redirect('dashboard')

    executed_count = 0
    total_used_this_run = 0

    for alloc in allocations:
        stock = alloc.stock
        side = request.POST.get(f'side_{alloc.id}')
        quantity = request.POST.get(f'qty_{alloc.id}')

        if not side or not quantity or int(quantity) <= 0:
            continue   # is stock ko skip kiya user ne

        quantity = int(quantity)
        current_price = stock.current_price
        order_value = float(current_price) * quantity

        # RISK CHECK: Basket ka overall fund limit
        if basket.allocated_fund and (float(basket.fund_used) + order_value) > float(basket.allocated_fund):
            messages.error(request, f"🚫 {stock.symbol}: Basket fund limit exceeded! Skipped.")
            continue

        # RISK CHECK: Is stock ka apna max_fund limit (basket ke andar)
        if (float(alloc.fund_used) + order_value) > float(alloc.max_fund):
            messages.error(request, f"🚫 {stock.symbol}: Per-stock fund limit (₹{alloc.max_fund}) exceeded! Skipped.")
            continue

        # RISK CHECK: Wallet balance (sirf BUY ke liye)
        if side == 'BUY' and float(wallet.balance) < order_value:
            messages.error(request, f"❌ {stock.symbol}: Insufficient wallet balance! Skipped.")
            continue

        # ---- Execute karo (buy_stock/sell_stock jaisा seedha wallet+portfolio update) ----
        portfolio, created = Portfolio.objects.get_or_create(
            user=request.user, stock=stock,
            defaults={'quantity': 0, 'average_buy_price': current_price}
        )

        # RISK CHECK: Short selling ke liye margin available hai kya? (NEW)
        # Pehle ye check individual sell_stock me tha, basket execute me missing tha
        needed_short_margin = None
        if side == 'SELL' and portfolio.quantity <= 0:
            needed_short_margin = required_margin_for_short(current_price, quantity, wallet)
            if available_margin(wallet) < needed_short_margin:
                messages.error(request, f"🚫 {stock.symbol}: Insufficient margin for short sell! Need ₹{needed_short_margin:.2f}, available ₹{available_margin(wallet):.2f}. Skipped.")
                continue

        if side == 'BUY':
            wallet.balance = float(wallet.balance) - order_value
            total_old_value = float(portfolio.quantity) * float(portfolio.average_buy_price)
            total_new_value = total_old_value + order_value
            portfolio.quantity += quantity
            portfolio.average_buy_price = total_new_value / portfolio.quantity if portfolio.quantity != 0 else 0
        else:  # SELL
            wallet.balance = float(wallet.balance) + order_value
            if portfolio.quantity > 0:
                portfolio.quantity -= quantity
            else:
                # NEW: Short position ke liye margin block karo
                wallet.margin_used = float(wallet.margin_used) + needed_short_margin
                total_old_value = abs(portfolio.quantity) * float(portfolio.average_buy_price)
                total_new_value = total_old_value + order_value
                new_quantity = portfolio.quantity - quantity
                portfolio.average_buy_price = total_new_value / abs(new_quantity) if new_quantity != 0 else 0
                portfolio.quantity = new_quantity

        portfolio.save()

        Transaction.objects.create(
            user=request.user, stock=stock, transaction_type=side,
            quantity=quantity, price=current_price
        )

        BasketItem.objects.create(
            basket=basket, stock=stock, side=side,
            quantity=quantity, entry_price=current_price
        )

        alloc.fund_used = float(alloc.fund_used) + order_value
        alloc.save()

        basket.fund_used = float(basket.fund_used) + order_value
        basket.total_invested = float(basket.total_invested) + order_value
        executed_count += 1

    wallet.save()
    basket.save()

    if executed_count > 0:
        messages.success(request, f"🧺 Basket '{basket.name}' executed! {executed_count} order(s) placed.")
    else:
        messages.warning(request, "Koi bhi order execute nahi hua (fund limits ya quantity check fail).")

    return redirect('dashboard')