# Stopwords

`en.txt`, `fr.txt`, `it.txt`, `pt.txt`: the Snowball stopword lists as PostgreSQL ships them
(`src/backend/snowball/stopwords/`, BSD licence), in the form the NLTK stopwords corpus distributes them.
Hybrid search (`memhub.storage.rules.query_keywords`) drops these words from the keyword leg; a project adds its
own with `search.stopwords` in `memhub.yaml`.
