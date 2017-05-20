import os
import pandas as pd
import numpy as np

from cellcommdb.api import current_dir
from cellcommdb.extensions import db
from cellcommdb.models import *
from cellcommdb.api import create_app


class Collector(object):
    def __init__(self, app):
        self.app = app

    def gene(self, gene_file=None):

        if not gene_file:
            gene_file = os.path.join(current_dir, 'data', 'gene_table.csv')

        with self.app.app_context():
            # Query for existing genes

            existing_genes = db.session.query(Gene.ensembl).all()
            existing_genes = [g[0] for g in existing_genes]

            # Query for proteins in order to join
            proteins = db.session.query(Protein.uniprot, Protein.id).all()
            proteins = {p[0]: p[1] for p in proteins}

            gene_df = pd.read_csv(gene_file, quotechar='"')

            # Remove genes already in db
            gene_df = gene_df[gene_df['ensembl'].apply(
                lambda x: x not in existing_genes)]

            gene_df['protein_id'] = gene_df['protein_uniprot'].replace(proteins)
            gene_df['protein_id'] = gene_df['protein_id'].apply(
                lambda x: x if isinstance(x, int) else np.nan)
            gene_df[['ensembl', 'name', 'protein_id']].to_sql(
                name='gene', if_exists='append', con=db.engine, index=False)

    def _get_column_table_names(self, model):
        colum_names = db.session.query(model).statement.columns
        return colum_names

    def _remove_not_defined_columns(self, data_frame, defined_columns):
        data_frame_keys = data_frame.keys()

        for key in data_frame_keys:
            if key not in defined_columns:
                data_frame.drop(key, axis=1, inplace=True)

        return data_frame

    def _get_db_existent_proteines(self, df_proteine):
        db_proteines = db.session.query(Protein).all()
        csv_uniprots = df_proteine['uniprot'].tolist()

        db_existent_uniprots = []
        for db_proteine in db_proteines:
            if db_proteine.uniprot in csv_uniprots:
                db_existent_uniprots.append(db_proteine)

        return db_existent_uniprots

    def _get_existent_proteines(self, df_proteine):
        db_proteines = pd.read_sql_table(table_name='protein', con=db.engine)
        csv_uniprots = df_proteine['uniprot'].tolist()

        db_proteines = db_proteines[db_proteines['uniprot'].apply(
            lambda x: x in csv_uniprots)]

        return db_proteines

    def table_to_dict(self, table):
        a = []

        for row in table:
            d = {}
            for column in row.__table__.columns:
                d[column.name] = str(getattr(row, column.name))
            a.append(d)

        return a

    def protein(self, protein_file=None):
        # Convert to boolean
        bools = ['transmembrane', 'secretion', 'peripheral', 'receptor',
                 'receptor_highlight', 'adhesion', 'other', 'transporter',
                 'secreted_highlight']

        with self.app.app_context():
            if not protein_file:
                protein_file = os.path.join(current_dir, 'data', 'protein.csv')

            prot_df = pd.read_csv(protein_file)

            prot_df['id'] = prot_df['id'].apply(lambda x: np.nan)

            colum_names = self._get_column_table_names(Protein)

            prot_df = self._remove_not_defined_columns(prot_df, colum_names)

            prot_df[bools] = prot_df[bools].astype(bool)

            db_repeat_proteines_df = self._get_existent_proteines(prot_df)

            all_prot_df = prot_df.append(db_repeat_proteines_df)

            unique_prots = all_prot_df.drop_duplicates(subset=['uniprot'])

            def merge_protein_values(row):
                proteine = all_prot_df[all_prot_df['uniprot'] == row['uniprot']]

                def setNonEmptyStrings(protRow):
                    row[protRow.notnull()] = protRow[protRow.notnull()]

                proteine.apply(
                    setNonEmptyStrings, axis=1
                )

                row[bools] = proteine[bools].any()

                return row

            unique_prots = unique_prots.apply(

                merge_protein_values, axis=1
            )

            new_uniprots = unique_prots[unique_prots['id'].isnull()]

            new_uniprots.drop('id', axis=1, inplace=True)
            new_uniprots.to_sql(name='protein', if_exists='append', con=db.engine, index=False)

            for index, unique_prot in unique_prots[unique_prots['id'].notnull()].iterrows():

                protein = db.session.query(Protein).get(unique_prot['id'])
                for key, value in unique_prot.to_dict().iteritems():

                    if pd.isnull(value):
                        setattr(protein, key, None)
                    else:
                        setattr(protein, key, value)

                    db.session.commit()

    def complex(self, complex_file=None):

        with self.app.app_context():
            if not complex_file:
                complex_file = os.path.join(current_dir, 'data', 'complex.csv')

            existing_complexes = db.session.query(Complex.name).all()
            existing_complexes = [c[0] for c in existing_complexes]
            proteins = db.session.query(Protein.uniprot, Protein.id).all()
            proteins = {p[0]: p[1] for p in proteins}

            # Read in complexes
            complex_df = pd.read_csv(complex_file, quotechar='"', na_values="-")
            complex_df.dropna(axis=1, inplace=True, how='all')

            # Get complex composition info
            complete_indices = []
            complex_map = {}
            for index, row in complex_df.iterrows():
                missing = False
                protein_id_list = []
                for protein in ['protein_1_id', 'protein_2_id',
                                'protein_3_id', 'protein_4_id']:
                    if not pd.isnull(row[protein]):
                        protein_id = proteins.get(row[protein])
                        if protein_id is None:
                            missing = True
                        else:
                            protein_id_list.append(protein_id)
                if not missing:
                    complex_map[row['name']] = protein_id_list
                    complete_indices.append(index)

            # Insert complexes
            if not complex_df.empty:
                # Remove unwanted columns
                removal_columns = list(filter(
                    lambda x: 'protein_' in x or 'Name_' in x or 'Unnamed' in x,
                    complex_df.columns))
                removal_columns += ['comments']
                complex_df.drop(removal_columns, axis=1, inplace=True)

                # Remove rows with missing complexes
                complex_df = complex_df.iloc[complete_indices, :]

                # Convert ints to bool
                bools = ['receptor', 'receptor_highlight', 'adhesion', 'other',
                         'transporter', 'secreted_highlight']
                complex_df[bools] = complex_df[bools].astype(bool)

                # Drop existing complexes
                complex_df = complex_df[complex_df['name'].apply(
                    lambda x: x not in existing_complexes)]

                complex_df.to_sql(name='complex', if_exists='append',
                                  con=db.engine, index=False)

            # Now find id's of new complex rows
            new_complexes = db.session.query(Complex.name, Complex.id).all()
            new_complexes = {c[0]: c[1] for c in new_complexes}

            # Build set of complexes
            complex_set = []
            for complex_name in complex_map:
                complex_id = new_complexes[complex_name]
                for protein_id in complex_map[complex_name]:
                    complex_set.append((complex_id, protein_id))

            # Insert complex composition
            complex_set_df = pd.DataFrame(complex_set,
                                          columns=['complex_id', 'protein_id'])
            complex_set_df.to_sql(
                name='complex_composition', if_exists='append',
                con=db.engine, index=False)


if __name__ == "__main__":
    app = create_app()
    collector = Collector(app)
    collector.complex()
