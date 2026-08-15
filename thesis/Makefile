PANDOC ?= pandoc
PDF_ENGINE ?= xelatex

.PHONY: check html

check:
	$(PANDOC) thesis.md --metadata-file=metadata.yaml --bibliography=references.bib --citeproc -t native -o /dev/null

html:
	$(PANDOC) thesis.md --metadata-file=metadata.yaml --bibliography=references.bib --citeproc --toc --number-sections -s -o thesis.html

# The final PDF target will be added once template.tex is finalized against CODE formatting requirements.
