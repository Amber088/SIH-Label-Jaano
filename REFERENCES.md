# LabelJaano — references and citations

Copy-paste source for the RESEARCH AND REFERENCES slide. Everything here is a source
the project genuinely encodes or depends on — nothing padded. Each regulation below
maps to an actual rulepack in `rulepacks/`, and the pack's `source_note` records which
values are verbatim and which are engineered detectors.

---

## A. Primary legal sources (the regulations the rulepacks encode)

**1. Legal Metrology (Packaged Commodities) Rules, 2011**
Department of Consumer Affairs, Legal Metrology Division, Government of India.
As amended, notably by G.S.R. 629(E) dated 23 June 2017.
Used for: Rule 6 mandatory declarations, the Rule 6(1)(e) proviso excluding alcohol
retail pricing, and the Rule 7(2) Table-I minimum font heights.
Pack: `legal_metrology_2011`, version 2011.r2022.v2 — 10 declarations.
Source: indiacode.nic.in · consumeraffairs.nic.in · legalmetrology.gov.in

**2. Food Safety and Standards (Labelling and Display) Regulations, 2020**
Food Safety and Standards Authority of India (FSSAI).
Used for: mandatory particulars and display requirements on pre-packaged food.
Pack: `fssai_food_2020` — 9 declarations.
Source: fssai.gov.in

**3. Food Safety and Standards (Packaging and Labelling) Regulations, 2011**
FSSAI. As amended through 2017.
Used for: product-specific declarations gated on traits — added colour, added flavour,
added MSG, caffeine, aspartame, artificial sweetener, irradiation, pan masala.
Pack: `fssai_packaging_labelling_2011`, version 2011.r2017 — 8 declarations.
Source: fssai.gov.in

**4. Food Safety and Standards (Contaminants, Toxins and Residues) Regulations, 2011**
FSSAI.
Used for: the reference-only tier — maximum permitted levels of metals, mycotoxins and
residues. 5 limits, 0 scored declarations, because none can be verified from a
photograph.
Pack: `fssai_contaminants_2011`.
Source: fssai.gov.in

**5. Food Safety and Standards (Alcoholic Beverages Standards) Regulations, 2018**
FSSAI.
Used for: Part-5 labelling provisions, the statutory warning and its mandated size, the
ABV tolerance, and prohibited claims. This pack overrides the base MRP declaration.
Pack: `fssai_alcoholic_2018` — 11 declarations, 3 reference standards.
Source: fssai.gov.in

**6. Food Safety and Standards (Fortification of Foods) Regulations, 2018**
FSSAI.
Used for: the +F logo and fortification claim wording. Schedule I fortificant levels are
surfaced as a reference standard only.
Pack: `fssai_fortification_2018` — 3 declarations, 1 reference standard.
Source: fssai.gov.in

**7. Food Safety and Standards (Organic Foods) Regulations, 2017**
FSSAI.
Used for: Regulation 5(1) organic-status information and the FSSAI Jaivik Bharat logo as
the baseline mark; NPOP / PGS-India certification marks modelled as advisory.
Pack: `fssai_organic_2017` — 3 declarations, 1 reference standard.
Source: fssai.gov.in · jaivikbharat.fssai.gov.in

**8. Food Safety and Standards (Health Supplements, Nutraceuticals, Food for Special
Dietary Use, Food for Special Medical Purpose and Prebiotic and Probiotic Food)
Regulations, 2016**
FSSAI. Regulations 6 to 9.
Used for: the mandatory category words, the medicinal-use advisory, the blanket
prohibition on disease claims, and the out-of-reach-of-children statement. Schedules
I–VIII actives are reference standards only.
Pack: `fssai_nutraceutical_2016` — 4 declarations, 2 reference standards.
Source: fssai.gov.in

**9. FSSAI Food Safety Compliance System (FoSCoS)**
Used for: the 14-digit FSSAI licence / registration number format.
Source: foscos.fssai.gov.in

---

## B. International and comparative standards

**10. Codex Alimentarius CXS 1-1985** — General Standard for the Labelling of
Prepackaged Foods, FAO/WHO Codex Alimentarius Commission. The international baseline
against which the Indian mandatory-particulars list can be compared.
Source: fao.org/fao-who-codexalimentarius

**11. Regulation (EU) No 1169/2011, Article 13(2) and Annex IV** — European Parliament
and Council, on the provision of food information to consumers. Mandates a minimum
x-height of 1.2 mm for mandatory particulars. This is the international analogue of
Rule 7(2) and the precedent for measuring font height as a compliance criterion rather
than treating it as cosmetic.
Source: eur-lex.europa.eu

---

## C. Technical references (the implementation)

**12. PaddleOCR** — text detection and glyph-level bounding boxes; the geometric input to
the font-height measurement. github.com/PaddlePaddle/PaddleOCR

**13. OpenCV ArUco module** — fiducial marker detection and homography estimation, used
to establish the pixel-to-millimetre scale from a marker or reference card in frame.
docs.opencv.org

**14. Google Gemini vision API** — structured field extraction from label photographs.
ai.google.dev

**15. FastAPI** — the service layer. fastapi.tiangolo.com

**16. NIST SP 800-63B** — Digital Identity Guidelines, Authentication and Lifecycle
Management, Section 5.1.1.2 on memorised-secret verifiers. The basis for iterated
key derivation rather than plain hashing. pages.nist.gov/800-63-3

**17. RFC 8018** — PKCS #5: Password-Based Cryptography Specification Version 2.1. The
PBKDF2 definition behind the 600,000-iteration password hash. rfc-editor.org

**18. RFC 7519** — JSON Web Token (JWT). rfc-editor.org

**19. RFC 8725** — JSON Web Token Best Current Practices. Why the algorithm is pinned
and an expiry claim is mandatory rather than optional. rfc-editor.org

---

## D. Optional — only if you present the comparison verbally

Cite these only if you actually name them on stage. Do not list sources you have not
looked at; a judge who asks "what did Smart Consumer get wrong?" and gets silence costs
you more than the citation earns.

**Smart Consumer** — Department of Consumer Affairs / BIS barcode lookup app. Returns a
product record from a database; does not read the printed panel. consumeraffairs.nic.in

**Food Safety Connect (FSSAI)** — consumer grievance and complaint filing, not label
verification. fssai.gov.in

**Open Food Facts** — open product database with nutrition and additive data, community
maintained. openfoodfacts.org

---

## How to say it on the slide

The deck already carries items 1–19 across two cards on slide 6. If you are trimming for
space, the ones that must survive are 1 (the statute you enforce), 2 (the FSSAI labelling
regulation), 4 (the reference-only tier that justifies the two-tier design), and 11 (the
international precedent for measuring font height).

Keep the caveat line. Each pack's `source_note` distinguishes values quoted verbatim from
the Rules from regexes that are engineered starting points, and the packs are an
enforcement aid, not a legal opinion. Saying this first is stronger than being asked.

