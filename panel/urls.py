from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = 'Panel del campus · Red LoRa de emergencias'
admin.site.site_title = 'Panel del campus'
admin.site.index_title = 'Administración de datos'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('entrar/', auth_views.LoginView.as_view(template_name='emergencias/login.html'), name='login'),
    path('salir/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('emergencias.urls')),
]
