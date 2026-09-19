"""Single source of truth for assigning books and quizzes to difficulty bands."""

import re

from .services.arbookfinder import atos_category


SERIES_DIFFICULTY = {
    'Pete the Cat': 'graded',
    'Frog and Toad': 'bridge',
    'Yasmin': 'bridge',
    'The Big Bad Fox': 'bridge',
    'Kung Pow Chicken': 'bridge',
    'Monkey Me': 'bridge',
    'Dog Man': 'bridge',
    'Magic Tree House': 'early_chapter',
    'My Weird School': 'early_chapter',
    'Geronimo Stilton': 'early_chapter',
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
        if level <= 8:
            return 'early_chapter'
        return 'middle_chapter'
    if '典范英语' in normalized:
        return 'graded'
    for marker, category in SERIES_DIFFICULTY.items():
        if marker.casefold() in normalized.casefold():
            return category
    return current or ''
