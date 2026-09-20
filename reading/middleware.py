"""Language policy for the application."""
from django.conf import settings
from django.utils import translation


class StudentEnglishMiddleware:
    """Default to English and keep every student-facing page in English."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        student_page = request.path.startswith('/student/') or request.session.get('persona_kind') == 'student'
        request.student_english = student_page
        has_explicit_language = bool(request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME))
        if student_page or not has_explicit_language:
            translation.activate('en')
            request.LANGUAGE_CODE = 'en'
        return self.get_response(request)
