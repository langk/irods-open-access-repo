import hashlib
import json
import logging
import requests
import time
import os

from irodsManager.irodsUtils import get_zip_generator, zip_generator_faker, ExporterClient, ExporterState as Status

from requests_toolbelt.multipart.encoder import MultipartEncoder
from http import HTTPStatus
from multiprocessing import Pool

logger = logging.getLogger('iRODS to Dataverse')


class DataverseClient(ExporterClient):
    """Dataverse client to import datasets and files
    """

    def __init__(self, host, token, alias, irodsclient):
        """
        :param host: String IP of the dataverseManager's host
        :param token: String token credential
        :param alias: String Alias/ID of the dataverseManager where to import dataset & files
        :param irodsclient: irodsClient object - client to iRODS database
        """
        self.repository = "Dataverse"
        self.host = host
        self.alias = alias
        self.token = token

        self.irods_client = irodsclient
        self.session = irodsclient.session
        self.rulemanager = irodsclient.rulemanager

        self.pool = None
        self.pool_result = None
        self.irods_md5 = None

        self.dataset_deposit_url = None
        self.dataset_pid = None
        self.dataset_url = None

        self.upload_success = {}

        self.deletion = False
        self.restrict = False
        self.restrict_list = []

        self.zip_name = irodsclient.imetadata.title

    def create_dataset(self, md, data_export=False):
        logger.info(f"{'--':<10}Dataset - request creation")

        self.irods_client.update_metadata_status(Status.CREATE_EXPORTER.value, Status.CREATE_DATASET.value)
        url = f"{self.host}/api/dataverses/{self.alias}/datasets/"

        resp = requests.post(
            url,
            data=json.dumps(md),
            headers={'Content-type': 'application/json',
                     'X-Dataverse-key': self.token
                     },
        )
        if resp.status_code == HTTPStatus.CREATED.value:
            self.dataset_pid = resp.json()['data']['persistentId']
            self.dataset_url = f"{self.host}/dataset.xhtml?persistentId={self.dataset_pid}&version=DRAFT"
            self.dataset_deposit_url = f"{self.host}/api/datasets/:persistentId/add?persistentId={self.dataset_pid}"
            logger.info(f"{'--':<20}Dataset created with pid: {self.dataset_pid}")
        else:
            logger.error(f"{'--':<20}Create dataset failed")
            logger.error(resp.content)
            self.irods_client.update_metadata_status(Status.CREATE_DATASET.value, Status.CREATE_DATASET_FAILED.value)

        if not data_export and resp.status_code == HTTPStatus.CREATED.value:
            self.irods_client.update_metadata_status(Status.CREATE_DATASET.value, Status.FINALIZE.value)
            self._final_report()
            self.email_confirmation()

    def import_files(self, deletion=False, restrict=False, restrict_list=''):
        self.deletion = deletion
        self.restrict = restrict
        if len(restrict_list) > 0:
            self.restrict_list = restrict_list.split(",")

        if self.dataset_deposit_url is not None:
            self.irods_client.update_metadata_status(Status.CREATE_DATASET.value, Status.PREPARE_COLLECTION.value)
            self.pool = Pool(processes=1)
            self.pool_result = self.pool.apply_async(self.run_checksum, [self.irods_client.coll.path])

            size_bundle = self._prepare_zip()
            response = self._upload_zip_collection(size_bundle)

            validated_checksum = self._validate_checksum()
            validated_upload = self._validate_upload(response)
            if validated_checksum and validated_upload:
                if self.deletion:
                    self.rulemanager.rule_deletion(self.upload_success)
                self._final_report()
                self.email_confirmation()
                self.submit_dataset_for_review()
        else:
            logger.error(f"{'--':<20}Dataset unknown")
            self.irods_client.update_metadata_status(Status.CREATE_DATASET.value, Status.DATASET_UNKNOWN.value)

    def _prepare_zip(self):
        logger.info(f"{'--':<10}Prepare zip")

        self.irods_client.update_metadata_status(Status.PREPARE_COLLECTION.value, Status.ZIP_COLLECTION.value)
        irods_md5 = hashlib.md5()
        size_bundle = zip_generator_faker(self.irods_client, self.upload_success, irods_md5, self.restrict_list)
        md5_hexdigest = irods_md5.hexdigest()
        logger.info(f"{'--':<20}Buffer faker MD5: {md5_hexdigest}")

        return size_bundle

    def _upload_zip_collection(self, size_bundle):
        logger.info(f"{'--':<10}Upload zip")

        self.irods_client.update_metadata_status(Status.ZIP_COLLECTION.value, Status.UPLOAD_ZIPPED_COLLECTION.value)
        self.irods_md5 = hashlib.md5()
        bundle_iterator = get_zip_generator(self.irods_client, self.upload_success,
                                            self.irods_md5, self.restrict_list, size_bundle)
        json_data = {"restrict": self.restrict}
        multipart_encoder = MultipartEncoder(
            fields={'jsonData': json.dumps(json_data),
                    'file': (self.zip_name, bundle_iterator)
                    }
        )
        resp = requests.post(
            self.dataset_deposit_url,
            data=multipart_encoder,
            headers={'Content-Type': multipart_encoder.content_type,
                     'X-Dataverse-key': self.token
                     },
        )

        return resp

    def _validate_checksum(self):
        logger.info(f"{'--':<10}Validate checksum")

        self.irods_client.update_metadata_status(Status.UPLOAD_ZIPPED_COLLECTION.value, Status.VALIDATE_CHECKSUM.value)
        self.pool.close()
        self.pool.join()
        chksums = self.pool_result.get()
        count = 0
        validated = False
        for k in self.upload_success.keys():
            if self.upload_success[k] == chksums[k]:
                self.upload_success.update({k: True})
                count += 1
        if count == len(self.upload_success):
            validated = True
            logger.info(f"{'--':<20}iRODS & buffer SHA-256 checksum: validated")
        else:
            logger.error(f"{'--':<20}SHA-256 checksum: failed")
            self.irods_client.update_metadata_status(Status.VALIDATE_UPLOAD.value, Status.UPLOAD_CORRUPTED.value)

        return validated

    def _validate_upload(self, resp):
        logger.info(f"{'--':<10}Validate upload")

        validated = False
        self.irods_client.update_metadata_status(Status.VALIDATE_CHECKSUM.value, Status.VALIDATE_UPLOAD.value)
        md5_hexdigest = self.irods_md5.hexdigest()
        logger.info(f"{'--':<20}Buffer MD5: {md5_hexdigest}")

        if resp.status_code == HTTPStatus.OK.value:
            md5_dataverse = resp.json()['data']['files'][0]['dataFile']['md5']
            logger.info(f"{'--':<20}Dataverse MD5: {md5_dataverse}")
            if md5_dataverse == md5_hexdigest:
                validated = True
                logger.info(f"{'--':<30}Checksum MD5 validated")
                self.irods_client.update_metadata_status(Status.VALIDATE_UPLOAD.value, Status.FINALIZE.value)
            else:
                logger.error(f"{'--':<30}Checksum MD5 match: False")
                logger.error(f"{'--':<30}{resp.content.decode('utf-8')}")
                self.irods_client.update_metadata_status(Status.VALIDATE_UPLOAD.value, Status.UPLOAD_CORRUPTED.value)
        else:
            logger.error(f"{'--':<30}{resp.content.decode('utf-8')}")
            self.irods_client.update_metadata_status(Status.VALIDATE_UPLOAD.value, Status.UPLOAD_FAILED.value)

        return validated

    def _final_report(self):
        logger.info(f"{'--':<10}Report final progress")
        self.irods_client.add_metadata('externalPID', self.dataset_pid, "Dataverse")
        self.irods_client.update_metadata_status(Status.FINALIZE.value, Status.EXPORTED.value)
        time.sleep(5)
        self.irods_client.remove_metadata(Status.ATTRIBUTE.value, f"Dataverse:{Status.EXPORTED.value}")
        logger.info(f"{'--':<10}Export Done")

    def email_confirmation(self):
        host = os.environ['DH_MAILER_HOST']
        user = os.environ['DH_MAILER_USERNAME']
        pwd = os.environ['DH_MAILER_PASSWORD']
        from_address = "datahub@maastrichtuniversity.nl"

        endpoint = "http://" + host + "/email/send"

        template_options = {
            "TITLE": self.irods_client.imetadata.title,
            "DESCRIPTION": self.irods_client.imetadata.description,
            "CREATOR": self.irods_client.imetadata.creator,
            "DATE": self.irods_client.imetadata.date,
            "BYTESIZE": self.irods_client.imetadata.bytesize,
            "NUMFILES": self.irods_client.imetadata.numfiles,
            "PID": self.irods_client.imetadata.pid,
            "TIMESTAMP": time.strftime("%d-%m-%Y %H:%M:%S"),
            "DEPOSITOR": self.irods_client.imetadata.depositor,

            "REPOSITORY": self.repository,
            "EXTERNAL_PID": self.dataset_pid,
            "DATASET_URL": self.dataset_url
        }

        data_user = {
            "language": "en",
            "templateName": "OpenAccess_export_confirmation",
            "templateOptions": template_options,
            "emailOptions": {
                "from": from_address,
                "to": self.irods_client.imetadata.depositor,
            }
        }

        # Post the e-mail confirmation to the user
        resp_user = requests.post(endpoint, json=data_user, auth=(user, pwd))

        if resp_user.status_code == HTTPStatus.OK.value:
            logger.info(f"Reporting e-mail confirmation sent to {self.irods_client.imetadata.depositor}")
        else:
            logger.error(resp_user.status_code)
            logger.error(resp_user.content)

    def submit_dataset_for_review(self):
        logger.info(f"{'--':<10}Dataset - request review")

        url = f"{self.host}/api/datasets/:persistentId/submitForReview?persistentId={self.dataset_pid}"

        resp = requests.post(
            url,
            headers={'Content-type': 'application/json',
                     'X-Dataverse-key': self.token
                     },
        )
        if resp.status_code == HTTPStatus.OK.value:
            logger.info(f"Dataset have been submitted for review: {self.dataset_url}")
        else:
            logger.error(f"{'--':<20}Create dataset failed")
            logger.error(resp.status_code)
            logger.error(resp.content)
