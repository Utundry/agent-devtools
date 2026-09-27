# Stage D1 semantic extractor foundation

This slice keeps semantic extraction lightweight and language-independent at the core. Extractors emit normalized definitions and relations; ranking and relation resolution consume those facts without language-specific branches.

Brace-family language differences live in small declarative profiles. Unknown formats continue to fall back to explicit semantic anchors, structural selectors, headings or bounded text windows. No external parser dependency is required.

`context affected` distinguishes directly changed `requiredPaths` from one-hop `preferredPaths`. Required paths reserve briefing representation before ordinary score/diversity filling so a changed file cannot silently disappear under the context budget.
