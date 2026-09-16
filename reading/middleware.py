"""Language policy for student-facing pages."""
from django.utils import translation


class StudentEnglishMiddleware:
    """Keep student login and authenticated student pages in English."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        student_page = request.path.startswith('/student/login/') or request.session.get('persona_kind') == 'student'
        request.student_english = student_page
        if student_page:
            translation.activate('en')
            request.LANGUAGE_CODE = 'en'
        return self.get_response(request)
