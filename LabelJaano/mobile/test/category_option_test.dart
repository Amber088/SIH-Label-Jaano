/// Unit tests for [CategoryOption] — the shape `GET /categories` is decoded into.
///
/// This file replaces the Flutter template's `widget_test.dart`, which tested a
/// counter app that never existed in this project: it referenced `MyApp` (the root
/// widget here is `LabelJaanoApp`) and so had been failing to compile since the first
/// commit, contributing the only two *errors* in `flutter analyze`.
///
/// What is worth testing here is the decode, not a widget. The picker used to be a
/// hardcoded list of four categories whose hints were written by hand and had drifted
/// — three of them claimed "Legal Metrology base pack" for categories that actually
/// pull four to six packs. The fix was to derive every hint from the numbers the
/// server reports, so these tests pin the derivation rather than the wording.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:label_jaano/core/config.dart';

/// A `CategoryOut` exactly as the backend emits it (see `app/schemas.py`).
const Map<String, dynamic> _beverage = {
  'id': 'beverage',
  'label': 'Beverage',
  'packs': [
    'legal_metrology_2011',
    'fssai_contaminants_2011',
    'fssai_food_2020',
    'fssai_fortification_2018',
    'fssai_organic_2017',
    'fssai_packaging_labelling_2011',
  ],
  'declarations': 33,
  'authorities': [
    'Department of Consumer Affairs — Legal Metrology Division',
    'Food Safety and Standards Authority of India (FSSAI)',
  ],
};

void main() {
  test('a category carries the server\'s own pack and declaration counts', () {
    final c = CategoryOption.fromJson(_beverage);
    expect(c.id, 'beverage');
    expect(c.label, 'Beverage');
    expect(c.packs.length, 6);
    expect(c.declarations, 33);
    expect(c.authorities.length, 2);
  });

  test('the hint is derived, so it cannot contradict the packs', () {
    final hint = CategoryOption.fromJson(_beverage).hint!;
    // The numbers, not a hand-written claim about which pack applies.
    expect(hint, contains('33 declarations'));
    expect(hint, contains('6 packs'));
    // The base regulator leads, and the rest are counted rather than listed.
    expect(hint, contains('Department of Consumer Affairs'));
    expect(hint, contains('+1'));
  });

  test('a long authority name is shortened to its abbreviation', () {
    final c = CategoryOption.fromJson({
      'id': 'packaged_food',
      'label': 'Packaged food',
      'packs': ['fssai_food_2020'],
      'declarations': 1,
      'authorities': ['Food Safety and Standards Authority of India (FSSAI)'],
    });
    expect(c.hint, contains('FSSAI'));
    // Singular, because "1 declarations · 1 packs" reads like a bug.
    expect(c.hint, contains('1 declaration ·'));
    expect(c.hint, contains('1 pack ·'));
  });

  test('a malformed or partial category decodes rather than throwing', () {
    // An older server, or a field the app does not know about yet. Losing the whole
    // picker over one unexpected payload would be worse than showing a bare label.
    final c = CategoryOption.fromJson({'id': 'mystery'});
    expect(c.id, 'mystery');
    expect(c.label, 'mystery', reason: 'falls back to the id, never empty');
    expect(c.packs, isEmpty);
    expect(c.declarations, 0);
    expect(c.hint, isNull, reason: 'nothing true to say, so say nothing');
  });

  test('auto-detect is the absence of a category, not a category', () {
    expect(CategoryOption.autoDetect.id, '');
    expect(CategoryOption.autoDetect.packs, isEmpty);
    // The offline fallback leads with it, so the picker is usable with no server.
    expect(AppInfo.fallbackCategories.first.id, '');
    expect(AppInfo.fallbackCategories.length, lessThan(4),
        reason: 'the fallback is a stopgap; the server owns the real list');
  });
}
