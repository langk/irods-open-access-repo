import logging
import os
import sys

from dataverseManager.dataverseClient import DataverseClient
from dataverseManager.dataverseMetadataMapper import MetadataMapper

logger = logging.getLogger('iRODS to Dataverse')


class DataverseExporter:
    def __init__(self):
        self.repository = "Dataverse"
        self.irods_client = None
        self.metadata_mapper = None
        self.exporter_client = None

    def init_export(self, irods_client, data):
        self.irods_client = irods_client
        try:
            self.do_export(data['dataverse_alias'], data['depositor'],
                           data['delete'], data['restrict'],
                           data['data_export'], data['restrict_list'])
        except:
            # Warning: catch all the unexpected error during export to perform clean-up
            print("Unexpected error:", sys.exc_info()[0])
            # Remove all temporary progress export AVU
            self.irods_client.status_cleanup()
            # Still raise the error
            raise

    def do_export(self, alias, depositor, delete=False, restrict=False, data_export=False, restrict_list=""):
        # Metadata
        logger.info("Metadata")
        self.metadata_mapper = MetadataMapper(self.irods_client.imetadata, depositor)
        md = self.metadata_mapper.read_metadata()

        # Dataverse
        logger.info("Dataverse")
        self.exporter_client = DataverseClient(os.environ['DATAVERSE_HOST'],
                                               os.environ['DATAVERSE_TOKEN'],
                                               alias, self.irods_client)
        self.exporter_client.create_dataset(md, data_export)
        if data_export:
            self.exporter_client.import_files(delete, restrict, restrict_list)

        # Cleanup
        self.irods_client.rulemanager.rule_close()
        self.irods_client.session.cleanup()
