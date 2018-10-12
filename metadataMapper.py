import json
import logging

logger = logging.getLogger('iRODS to Dataverse')

'''
TODO
self.protocol = None
 '''


class MetadataMapper:

    def __init__(self, imetadata):
        self.imetadata = imetadata
        self.dataset_json = None
        self.md = None

    # def __init__(self, title, creator, description, date, pid):
    #     self.title = title
    #     self.creator = creator
    #     self.description = description
    #     self.date = date
    #     self.pid = pid
    #     self.dataset_json = None

    def read_metadata(self):
        with open('template.json') as f:
            self.dataset_json = json.load(f)

        self.md = self.dataset_json['datasetVersion']

        # pid = meta_dict.get("PID")
        print("--\t" + self.imetadata.pid)
        logger.info("--\t" + self.imetadata.pid)

        pid = self.imetadata.pid.split("/")
        self.update_pid(self.md, pid[0], pid[1])

        self.add_author(self.imetadata.creator)

        if self.imetadata.description is None:
            self.add_description("")
        else:
            self.add_description(self.imetadata.description)

        self.add_date(self.imetadata.date)

        self.add_title(self.imetadata.title)

        self.add_subject()

        contacts = []
        contact_email = self.add_contact_email(self.imetadata.creator)
        contacts.append(contact_email)

        for c in self.imetadata.contact:
            if len(c) != 0:
                print(type(c))
                pub = self.add_contact(c.get("firstName")+" "+c.get("lastName"), c.get("email"), c.get("affiliation"))
                contacts.append(pub)

        self.add_contacts(contacts)

        keywords = []
        if self.imetadata.tissue:
            keyword = self.add_keyword(self.imetadata.tissue.get("name"), self.imetadata.tissue.get("vocabulary"),
                                       self.imetadata.tissue.get("uri"))
            keywords.append(keyword)

        if self.imetadata.technology:
            keyword = self.add_keyword(self.imetadata.technology.get("name"), self.imetadata.technology.get("vocabulary"),
                                       self.imetadata.technology.get("uri"))
            keywords.append(keyword)

        if self.imetadata.organism:
            keyword = self.add_keyword(self.imetadata.organism.get("name"), self.imetadata.organism.get("vocabulary"),
                                       self.imetadata.organism.get("uri"))
            keywords.append(keyword)

        for f in self.imetadata.factors:
            keyword = self.add_keyword(f, "", "")
            keywords.append(keyword)

        self.add_keywords(keywords)

        publications = []
        for f in self.imetadata.articles:
            info = f.split("/")
            pub = self.add_publication(info[3] + info[4], info[2].strip(".org"), f)
            publications.append(pub)

        self.add_publications(publications)

        self.dataset_json['datasetVersion'] = self.md
        logger.info(json.dumps(self.dataset_json, indent=4))

        return self.dataset_json

    def update_pid(self, md, authority, identifier, hdl="hdl"):
        md["protocol"] = hdl
        md["authority"] = authority
        md["identifier"] = identifier

    def update_fields(self, new):
        fields = self.md["metadataBlocks"]["citation"]["fields"]
        fields.append(new)

    def add_author(self, author, affiliation, up=True):
        new = {
            "typeName": "author",
            "multiple": True,
            "value": [
                {
                    "authorAffiliation": {
                        "typeName": "authorAffiliation",
                        "multiple": False,
                        "value": affiliation,
                        "typeClass": "primitive"
                    },
                    "authorName": {
                        "typeName": "authorName",
                        "multiple": False,
                        "value": author,
                        "typeClass": "primitive"
                    }
                }
            ],
            "typeClass": "compound"
        }
        if up:
            self.update_fields(new)
        return new

    def add_author(self, author, up=True):
        new = {
            "typeName": "author",
            "multiple": True,
            "value": [
                {
                    "authorName": {
                        "typeName": "authorName",
                        "multiple": False,
                        "value": author,
                        "typeClass": "primitive"
                    }
                }
            ],
            "typeClass": "compound"
        }
        if up:
            self.update_fields(new)
        return new

    def add_title(self, title, up=True):
        new = {
            "typeName": "title",
            "multiple": False,
            "value": title,
            "typeClass": "primitive"
        }
        if up:
            self.update_fields(new)
        return new

    def add_description(self, description, up=True):
        new = {
            "typeName": "dsDescription",
            "multiple": True,
            "value": [
                {
                    "dsDescriptionValue": {
                        "typeName": "dsDescriptionValue",
                        "multiple": False,
                        "value": description,
                        "typeClass": "primitive"
                    }
                }
            ],
            "typeClass": "compound"
        }
        if up:
            self.update_fields(new)
        return new

    def add_subject(self, up=True):
        new = {
            "typeName": "subject",
            "multiple": True,
            "value": [
                "Medicine, Health and Life Sciences"
            ],
            "typeClass": "controlledVocabulary"
        }
        if up:
            self.update_fields(new)
        return new

    def add_date(self, date, up=True):
        new = {
            "typeName": "productionDate",
            "multiple": False,
            "value": date,
            "typeClass": "primitive"
        }
        if up:
            self.update_fields(new)
        return new

    def add_contacts(self, contacts, up=True):
        new = {
            "typeName": "datasetContact",
            "multiple": True,
            "value": contacts,
            "typeClass": "compound"
        }
        if up:
            self.update_fields(new)
        return new

    def add_contact_email(self, email):
        new = {
            "datasetContactEmail": {
                "typeName": "datasetContactEmail",
                "multiple": False,
                "value": email,
                "typeClass": "primitive"
            }
        }
        return new

    def add_contact(self, name, email, affiliation):
        new = {
            "datasetContactAffiliation": {
                "multiple": False,
                "typeClass": "primitive",
                "typeName": "datasetContactAffiliation",
                "value": affiliation
            },
            "datasetContactEmail": {
                "multiple": False,
                "typeClass": "primitive",
                "typeName": "datasetContactEmail",
                "value": email
            },
            "datasetContactName": {
                "multiple": False,
                "typeClass": "primitive",
                "typeName": "datasetContactName",
                "value": name
            }
        }
        return new

    def add_keywords(self, keywords, up=True):
        new = {
            "multiple": True,
            "typeClass": "compound",
            "typeName": "keyword",
            "value": keywords
        }
        if up:
            self.update_fields(new)
        return new

    def add_keyword(self, value, vocabulary, uri):
        new = {
            "keywordValue": {
                "multiple": False,
                "typeClass": "primitive",
                "typeName": "keywordValue",
                "value": value
            },
            "keywordVocabulary": {
                "multiple": False,
                "typeClass": "primitive",
                "typeName": "keywordVocabulary",
                "value": vocabulary
            },
            "keywordVocabularyURI": {
                "multiple": False,
                "typeClass": "primitive",
                "typeName": "keywordVocabularyURI",
                "value": uri
            }
        }
        return new

    def add_publications(self, publications, up=True):
        new = {
            "multiple": True,
            "typeClass": "compound",
            "typeName": "publication",
            "value": publications
        }
        if up:
            self.update_fields(new)
        return new

    def add_publication(self, value, doi, url):
        new = {
            "publicationIDNumber": {
                "multiple": False,
                "typeClass": "primitive",
                "typeName": "publicationIDNumber",
                "value": value
            },
            "publicationIDType": {
                "multiple": False,
                "typeClass": "controlledVocabulary",
                "typeName": "publicationIDType",
                "value": doi
            },
            "publicationURL": {
                "multiple": False,
                "typeClass": "primitive",
                "typeName": "publicationURL",
                "value": url
            }
        }
        return new


'''
    def read_metadata(self):
        # title = meta_dict.get("title")
        # creator = meta_dict.get("creator")
        # description = meta_dict.get("description")
        # date = meta_dict.get("date")

        # dataset_json = None
        with open('template.json') as f:
            self.dataset_json = json.load(f)

        md = self.dataset_json['datasetVersion']

        # pid = meta_dict.get("PID")
        print("--\t" + self.pid)
        logger.info("--\t" + self.pid)

        pid = self.pid.split("/")
        self.update_pid(md, pid[0], pid[1])

        new = self.add_author(self.creator)
        self.update_fields(md, new)

        new = self.add_description(self.description)
        self.update_fields(md, new)

        new = self.add_date(self.date)
        self.update_fields(md, new)

        new = self.add_title(self.title)
        self.update_fields(md, new)

        new = self.add_subject()
        self.update_fields(md, new)

        new = self.add_contact_email(self.creator)
        self.update_fields(md, new)

        self.dataset_json['datasetVersion'] = md
        logger.info(json.dumps(self.dataset_json, indent=4))

        return self.dataset_json

'''
