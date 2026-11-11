import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Crea la cuenta del responsable del campus a partir de RESPONSABLE_USUARIO y RESPONSABLE_PASSWORD.'

    def handle(self, *args, **opciones):
        usuario = os.environ.get('RESPONSABLE_USUARIO')
        password = os.environ.get('RESPONSABLE_PASSWORD')
        if not usuario or not password:
            self.stdout.write('RESPONSABLE_USUARIO/RESPONSABLE_PASSWORD no definidos; no se crea cuenta.')
            return
        User = get_user_model()
        if User.objects.filter(username=usuario).exists():
            self.stdout.write(f'La cuenta "{usuario}" ya existe.')
            return
        User.objects.create_superuser(usuario, os.environ.get('RESPONSABLE_EMAIL', ''), password)
        self.stdout.write(f'Cuenta "{usuario}" creada.')
