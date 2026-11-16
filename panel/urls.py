from django.contrib.auth import views as auth_views
from django.urls import include, path

# El sitio de administración de Django quedó fuera del alcance (SRS 1.2): las
# cuentas se crean al desplegar desde variables de entorno (S10) y los datos
# que llegan por radio no se editan a mano.
urlpatterns = [
    path('entrar/', auth_views.LoginView.as_view(template_name='emergencias/login.html'), name='login'),
    path('salir/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('emergencias.urls')),
]
