"""
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

__author__ = '3liz'
__date__ = '2019-02-15'
__copyright__ = '(C) 2019 by 3liz'

# This will get replaced with a git SHA1 when you do a git archive

__revision__ = '$Format:%H$'

import time

import processing
from db_manager.db_plugins import createDbPlugin
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterEnum,
    QgsProcessingOutputString,
    QgsExpressionContextUtils,
)

from .tools import *


class ImportSpatialLayerData(QgsProcessingAlgorithm):
    """
    """

    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.

    SPATIALLAYER = 'SPATIALLAYER'
    SOURCELAYER = 'SOURCELAYER'
    UNIQUEID = 'UNIQUEID'
    UNIQUELABEL = 'UNIQUELABEL'

    OUTPUT_STATUS = 'OUTPUT_STATUS'
    OUTPUT_STRING = 'OUTPUT_STRING'

    SPATIALLAYERS = []

    def name(self):
        return 'import_spatial_layer_data'

    def displayName(self):
        return self.tr('Import spatial layer data')

    def group(self):
        return self.tr('Manage')

    def groupId(self):
        return 'gobs_manage'

    def shortHelpString(self):
        short_help = tr(
            'This algorithm allows to import data from a QGIS spatial layer into the G-Obs database'
            '\n'
            '\n'
            'The G-Obs administrator must have created the needed spatial layer beforehand by addind the required items in the related database tables: gobs.actor_category, gobs.actor and gobs.spatial_layer.'
            '\n'
            '* Target spatial layer: choose one of the spatial layers available in G-Obs database'
            '\n'
            '* Source data layer: choose the QGIS vector layer containing the spatial data you want to import into the chosen spatial layer.'
            '\n'
            '* Unique identifier: choose the field containing the unique ID. It can be an integer or a text field, but must be unique.'
            '\n'
            '* Unique label: choose the text field containing the unique label of the layer feature. You could use the QGIS field calculator to create one if needed.'
            '\n'
        )
        return short_help

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return self.__class__()

    def initAlgorithm(self, config):
        """
        Here we define the inputs and output of the algorithm, along
        with some other properties.
        """
        # INPUTS
        connection_name = QgsExpressionContextUtils.globalScope().variable('gobs_connection_name')
        get_data = QgsExpressionContextUtils.globalScope().variable('gobs_get_database_data')

        # List of spatial_layer
        sql = '''
            SELECT id, sl_label
            FROM gobs.spatial_layer
            ORDER BY sl_label
        '''
        dbpluginclass = createDbPlugin( 'postgis' )
        connections = [c.connectionName() for c in dbpluginclass.connections()]
        data = []
        if get_data == 'yes' and connection_name in connections:
            [header, data, rowCount, ok, error_message] = fetchDataFromSqlQuery(
                connection_name,
                sql
            )
        self.SPATIALLAYERS = ['%s - %s' % (a[1], a[0]) for a in data]
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SPATIALLAYER,
                self.tr('Target spatial layer'),
                options=self.SPATIALLAYERS,
                optional=False
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.SOURCELAYER,
                self.tr('Source data layer'),
                optional=False
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.UNIQUEID,
                self.tr('Unique identifier'),
                parentLayerParameterName=self.SOURCELAYER
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.UNIQUELABEL,
                self.tr('Unique label'),
                parentLayerParameterName=self.SOURCELAYER,
                type=QgsProcessingParameterField.String
            )
        )

        # OUTPUTS
        # Add output for message
        self.addOutput(
            QgsProcessingOutputString(
                self.OUTPUT_STRING,
                self.tr('Output message')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        """
        Here is where the processing itself takes place.
        """
        # parameters
        # Database connection parameters
        connection_name = QgsExpressionContextUtils.globalScope().variable('gobs_connection_name')

        spatiallayer = self.SPATIALLAYERS[parameters[self.SPATIALLAYER]]
        sourcelayer = self.parameterAsVectorLayer(parameters, self.SOURCELAYER, context)
        uniqueid = self.parameterAsString(parameters, self.UNIQUEID, context)
        uniquelabel = self.parameterAsString(parameters, self.UNIQUELABEL, context)

        msg = ''
        status = 1

        # Get chosen spatial layer id
        id_spatial_layer = spatiallayer.split('-')[-1].strip()

        # Import data to temporary table
        feedback.pushInfo(
            self.tr('IMPORT SOURCE LAYER INTO TEMPORARY TABLE')
        )
        temp_schema = 'public'
        temp_table = 'temp_' + str(time.time()).replace('.', '')
        ouvrages_conversion = processing.run("qgis:importintopostgis", {
            'INPUT': parameters[self.SOURCELAYER],
            'DATABASE': connection_name,
            'SCHEMA': temp_schema,
            'TABLENAME': temp_table,
            'PRIMARY_KEY': 'gobs_id',
            'GEOMETRY_COLUMN': 'geom',
            'ENCODING': 'UTF-8',
            'OVERWRITE': True,
            'CREATEINDEX': False,
            'LOWERCASE_NAMES': False,
            'DROP_STRING_LENGTH': True,
            'FORCE_SINGLEPART': False
        }, context=context, feedback=feedback)
        feedback.pushInfo(
            self.tr('* Source layer has been imported into temporary table')
        )

        # Copy data to spatial_object
        feedback.pushInfo(
            self.tr('COPY IMPORTED DATA TO spatial_object')
        )
        sql = '''
            INSERT INTO gobs.spatial_object
            (so_unique_id, so_unique_label, geom, fk_id_spatial_layer)
            SELECT "%s", "%s", ST_Transform(ST_Buffer(geom,0), 4326) AS geom, %s
            FROM "%s"."%s"
            ;
        ''' % (
            uniqueid,
            uniquelabel,
            id_spatial_layer,
            temp_schema,
            temp_table
        )
        try:
            [header, data, rowCount, ok, error_message] = fetchDataFromSqlQuery(
                connection_name,
                sql
            )
            if not ok:
                status = 0
                msg = self.tr('* The following error has been raised') + '  %s' % error_message
                feedback.reportError(
                    msg
                )
            else:
                status = 1
                msg = self.tr('* Source data has been successfully imported !')
                feedback.pushInfo(
                    msg
                )
        except:
            status = 0
            msg = self.tr('* An unknown error occured while adding features to spatial_object table')
        finally:

            # Remove temporary table
            feedback.pushInfo(
                self.tr('DROP TEMPORARY DATA')
            )
            sql = '''
                DROP TABLE IF EXISTS "%s"."%s"
            ;
            ''' % (
                temp_schema,
                temp_table
            )
            [header, data, rowCount, ok, error_message] = fetchDataFromSqlQuery(
                connection_name,
                sql
            )
            if ok:
                feedback.pushInfo(
                    self.tr('* Temporary data has been deleted.')
                )
            else:
                feedback.reportError(
                    self.tr('* An error occured while droping temporary table') + ' "%s"."%s"' % (temp_schema, temp_table)
                )


        msg = self.tr('SPATIAL LAYER HAS BEEN SUCCESSFULLY IMPORTED !')

        return {
            self.OUTPUT_STATUS: status,
            self.OUTPUT_STRING: msg
        }
