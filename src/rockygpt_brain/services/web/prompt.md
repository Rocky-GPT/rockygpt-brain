# The web search

The only instruction sent to a model outside the pipeline's own three. It asks
for facts with their sources, because an answer built from the open web is
worth exactly what a reader can check.

The last line matters most: returning nothing is a valid answer, and a fact no
page supports is not.

---

Search the web and answer the query.

Return one entry per fact that answers it. `source` is the page URL the fact
came from, on its own with no surrounding text. `publishedAt` is the date that
page gives for the fact, or null when it gives none.

Return nothing rather than a fact no page supports.
