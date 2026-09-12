from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('buy/<int:stock_id>/', views.buy_stock, name='buy_stock'),
    path('sell/<int:stock_id>/', views.sell_stock, name='sell_stock'),
    path('history/', views.transaction_history, name='transaction_history'),
]