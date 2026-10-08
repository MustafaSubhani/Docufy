# Spark voice

Write in UK English. Use the spellings in `lexicon.yaml` (specialising, mobilisation, formalised, itemised, programme, organised, licence).

Sound like a senior delivery partner: specific, calm, and concrete. Name the client's systems and the next action. Prefer short sentences over slogans.

- Client documents may say "proposal" only when the archetype is `proposal`. Every other archetype uses its own `doc_type` and never the word proposal in a title.
- Guides address the reader directly ("you open the drawing") and do not say "for clients".
- Internal reports state the method, the result, and the source. They do not include company intro, Why Choose Us, or a contact banner.
- Company facts (name, phone, email, website, address, products) come from `facts.yaml`. Do not invent a different phone number or office.
- Use `{{client}}` and `{{client_possessive}}` in boilerplate. Do not hard-code a client name into a reusable section.
- The Path Forward is three paragraphs: what we assessed, that the work fits what the client already has, and the concrete first step.
- How We Move Forward is four step cards: Proposal Review, Technical Discovery, Contract Execution, Project Kickoff.
