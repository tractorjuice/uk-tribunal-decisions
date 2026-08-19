# Privacy and takedown policy

This repository republishes decisions of the First-tier Tribunal (Property
Chamber) in England and the Residential Property Tribunal Wales. Those
decisions are already published by the tribunals themselves. This document
explains what personal data is involved, why it is republished, and how to ask
for a record to be corrected or removed.

## What personal data this contains

Tribunal decisions are public documents, and they name people. Across the
collection that means, approximately:

- a full property address, including postcode, on essentially every record
- the names of applicants and respondents, many of whom are private
  individuals rather than companies or councils
- the names of the tribunal judges and members who heard the case
- sums of money awarded or in dispute, tied to a named party at a named address
- a full-text search index built from the decision text, which may surface any
  detail a decision happens to record

## Why it is published

Tribunal decisions are published so that the law is applied openly and so that
leaseholders, tenants, landlords, advisers and researchers can find out how
similar cases have been decided. The official sources publish decisions one at
a time and are difficult to search across. This project exists to make the same
public record searchable as a collection.

## Where this differs from the official sources

Aggregation changes things, and it is worth being explicit about it.

The tribunals publish individual decisions. This project publishes the whole
collection in one place, with a search index over the full text. That makes it
possible to search by a person's name or address in a way the official sites do
not readily allow. That is a meaningful difference, and it is why this policy
exists.

Two deliberate choices follow from it:

- **No per-decision pages.** The site generates browsable pages per category,
  region and year, but not one page per decision. A search engine can find the
  collection; it cannot index a page dedicated to an individual and their home
  address.
- **Hub pages list only recent decisions.** Each hub page shows the 50 most
  recent decisions in its group rather than the full set, so the collection is
  discoverable without publishing a crawlable directory of named individuals.

## Licensing is not the same as data protection

The decision data is licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/),
and this project complies with its attribution requirements.

The OGL is a copyright licence. It expressly does **not** extend to personal
data, and it does not authorise any particular processing of personal data
under UK GDPR. Those are separate questions, and being licensed under the OGL
does not settle them.

## Asking for a correction or removal

If you are named in a decision here and want it corrected or removed:

1. **Open an issue** on
   [the repository](https://github.com/tractorjuice/uk-tribunal-decisions/issues),
   or contact the maintainer through GitHub if you would rather not post
   publicly. Include the case reference or the URL of the record.
2. You do not need to explain why. A request is enough to have the record
   reviewed.
3. Records are removed from this mirror on request where the request is
   reasonable, and always where the decision has been removed or amended at
   source.

Please note this project is an unofficial mirror. It cannot amend the official
record. To correct the decision itself, contact the tribunal that issued it:

- England — [HM Courts & Tribunals Service](https://www.gov.uk/courts-tribunals/first-tier-tribunal-property-chamber)
- Wales — [Residential Property Tribunal Wales](https://residentialpropertytribunal.gov.wales)

If a decision is removed or amended at source, this mirror should follow. The
refresh pipeline runs weekly, but if you have had a decision removed upstream
and it is still here, please open an issue rather than waiting.

## Retention and refresh

The dataset is rebuilt from the official sources on a weekly schedule. Records
that disappear upstream are flagged rather than silently deleted, so that a
removal can be reviewed rather than being mistaken for a scraping failure.

## Not affiliated

This project is not affiliated with, endorsed by, or operated on behalf of
GOV.UK, HM Courts & Tribunals Service, the Welsh Government, or any tribunal.
