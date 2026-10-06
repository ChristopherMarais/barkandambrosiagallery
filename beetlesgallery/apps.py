from django.contrib.staticfiles.apps import StaticFilesConfig


class GalleryStaticFilesConfig(StaticFilesConfig):
    """collectstatic leaves out css/input.css, the Tailwind source that pixi run build-css turns into style.css.

    Its `@import "tailwindcss"` names a package, not a file, so the manifest storage could not resolve it and
    collectstatic (Docker build, deploys) would stop. The pages only load style.css.
    """

    ignore_patterns = [*StaticFilesConfig.ignore_patterns, "input.css"]
