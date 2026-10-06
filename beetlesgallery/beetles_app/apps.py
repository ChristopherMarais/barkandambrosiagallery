from django.apps import AppConfig

class BeetlesAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'beetlesgallery.beetles_app'

    def ready(self):
        # Import models to ensure signals register when the app boots
        import beetlesgallery.beetles_app.models
        import beetlesgallery.beetles_app.game_crops  # noqa: F401 (cuts a new batch's crops on the worker)