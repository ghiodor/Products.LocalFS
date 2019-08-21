
class XMLDocumentFactory:

    def __call__(self, id, file):
        try: from Products.ParsedXML.ParsedXML import ParsedXML
        except: return
        ob = ParsedXML(id=id, XMLstring=file.read(), namespaces=1, contentType='text/xml')
        return ob

    def save(self, ob, path):
        f = open(path, 'w')
        f.write(ob.index_html())
        f.close()
