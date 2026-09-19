from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('buy/<int:stock_id>/', views.buy_stock, name='buy_stock'),
    path('sell/<int:stock_id>/', views.sell_stock, name='sell_stock'),
    path('place-order/<int:stock_id>/', views.place_order, name='place_order'),
    path('cancel-order/<int:order_id>/', views.cancel_order, name='cancel_order'),
    path('portfolio/<int:portfolio_id>/modify-sl/', views.modify_position_sl, name='modify_position_sl'),
    path('basket/create/', views.create_basket, name='create_basket'),
    path('basket/<int:basket_id>/edit/', views.edit_basket, name='edit_basket'),
    path('basket/<int:basket_id>/update-trailing/', views.update_basket_trailing, name='update_basket_trailing'),
    path('basket/<int:basket_id>/add-stock/', views.add_stock_to_basket, name='add_stock_to_basket'),
    path('basket/allocation/<int:allocation_id>/remove/', views.remove_stock_from_basket, name='remove_stock_from_basket'),
    path('basket/<int:basket_id>/execute/', views.execute_basket, name='execute_basket'),
    path('basket/<int:basket_id>/close/', views.close_basket, name='close_basket'),
    path('history/', views.transaction_history, name='transaction_history'),
]