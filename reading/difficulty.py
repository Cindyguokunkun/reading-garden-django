"""Single source of truth for assigning books and quizzes to difficulty bands."""

import re

from .services.arbookfinder import atos_category


SERIES_DIFFICULTY = {
    'Pete the Cat': 'graded',
    'Frog and Toad': 'bridge',
    'Yasmin': 'bridge',
    'The Big Bad Fox': 'bridge',
    'Kung Pow Chicken': 'early_chapter',
    'Monkey Me': 'early_chapter',
    'Magic Tree House': 'middle_chapter',
    'My Weird School': 'middle_chapter',
    'Geronimo Stilton': 'middle_chapter',
    'Dog Man': 'middle_chapter',
}


def difficulty_category(*, series='', atos=None, current=''):
    """Return the shared five-band difficulty category for a book and its quiz."""
    if atos is not None:
        return atos_category(atos)
    normalized = series or ''
    match = re.search(r'(?:典范英语|Good English)\s*(\d+)', normalized, re.I)
    if match:
        level = int(match.group(1))
        if level <= 2:
            return 'graded'
        if level <= 4:
            return 'bridge'
        if level <= 6:
            return 'early_chapter'
        if level == 7:
            return 'middle_chapter'
        return 'upper_chapter'
    if '典范英语' in normalized:
        return 'graded'
    for marker, category in SERIES_DIFFICULTY.items():
        if marker.casefold() in normalized.casefold():
            return category
    return current or ''
