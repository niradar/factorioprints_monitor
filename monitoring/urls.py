# urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing, name='home'),
    path('user/<str:fp_user_id>/snapshot/', views.take_snapshot_view, name='take_snapshot'),
    path('user/<str:fp_user_id>/snapshot/status/', views.snapshot_status, name='snapshot_status'),
    path('user/<str:fp_user_id>/inbox/', views.inbox, name='inbox'),
    path('user/<str:fp_user_id>/inbox/mark-all-done/', views.mark_all_done, name='mark_all_done'),
    path('user/<str:fp_user_id>/blueprints/', views.blueprints_list, name='blueprints'),
    path('user/<str:fp_user_id>/blueprint/<int:blueprint_id>/', views.blueprint_detail, name='blueprint_detail'),
    path('user/<str:fp_user_id>/settings/', views.settings_page, name='settings'),
    path('user/<str:fp_user_id>/about/', views.about, name='about'),
    path(
        'user/<str:fp_user_id>/comment/<int:blueprint_id>/<str:comment_id>/toggle/',
        views.toggle_handled,
        name='toggle_handled',
    ),
]
