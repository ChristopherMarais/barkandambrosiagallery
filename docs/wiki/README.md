# Wiki pages

The source of the [project wiki](https://github.com/ChristopherMarais/barkandambrosiagallery/wiki): edit them here,
through a pull request like any other change, then publish them to the wiki:

```bash
git clone https://github.com/ChristopherMarais/barkandambrosiagallery.wiki.git /tmp/wiki
cp docs/wiki/*.md /tmp/wiki/ && rm /tmp/wiki/README.md
cd /tmp/wiki && git add -A && git commit -m "Update from docs/wiki" && git push
```
