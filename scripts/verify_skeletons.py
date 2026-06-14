"""Author + verify one runnable join-skeleton SPARQL per verified crosswalk.

Each skeleton returns COUNT(DISTINCT <shared key>) over the two (or more) named
graphs, applying the entry's IRI-normalization. Running it is an executable test
of that normalization (the #1 silent-failure mode) and a freshness check against
the stored ``verified_count``.

Usage:
    uv run python scripts/verify_skeletons.py            # run all
    uv run python scripts/verify_skeletons.py E3 F1 ...  # run a subset
    uv run python scripts/verify_skeletons.py --inject   # write passing
                                                         # queries into metadata
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from mcp_okn.sparql import run_sparql  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "metadata" / "crosswalks.json"


def g(kg: str) -> str:
    return f"<https://purl.org/okn/frink/kg/{kg}>"


# Common predicate IRIs
EXACT = "<http://www.w3.org/2004/02/skos/core#exactMatch>"
HASID = "<http://edamontology.org/has_identifier>"
SAMEAS = "<http://www.w3.org/2002/07/owl#sameAs>"
SEEALSO = "<http://www.w3.org/2000/01/rdf-schema#seeAlso>"
LABEL = "<http://www.w3.org/2000/01/rdf-schema#label>"
DBXREF = "<http://www.geneontology.org/formats/oboInOwl#hasDbXref>"
BL_OBJ = "<https://w3id.org/biolink/vocab/object>"
BL_SUBJ = "<https://w3id.org/biolink/vocab/subject>"
SUBCLASS = "<http://www.w3.org/2000/01/rdf-schema#subClassOf>"
# prokn's curated disease entity. Its biolink EFO_0000651 'DiseaseOrPhenotype'
# association nodes ALSO carry MONDO/OMIM/MedGen on seeAlso; scoping the oard<->prokn
# disease joins to up:Disease keeps them disease-entity-to-disease-entity.
UP_DISEASE = "<http://purl.uniprot.org/core/Disease>"

Q: dict[str, str] = {}

# --- CAS cluster -----------------------------------------------------------
Q["E3-cas-aopwiki"] = f"""
SELECT (COUNT(DISTINCT ?c2) AS ?n) WHERE {{
  GRAPH {g('biobricks-aopwiki')} {{ ?s <http://aopkb.org/aop_ontology#has_chemical_entity> ?cas . }}
  BIND(IRI(REPLACE(STR(?cas),'https://identifiers.org/cas/','http://identifiers.org/cas/')) AS ?c2)
  GRAPH {g('biobricks-toxcast')} {{ ?t {HASID} ?c2 . }}
}}"""

Q["B1-cas"] = f"""
SELECT (COUNT(DISTINCT ?cas) AS ?n) WHERE {{
  GRAPH {g('biobricks-ice')} {{ ?a {HASID} ?cas . }}
  GRAPH {g('biobricks-toxcast')} {{ ?b {HASID} ?cas . }}
  FILTER(STRSTARTS(STR(?cas),'http://identifiers.org/cas/'))
}}"""

Q["E1-cas"] = f"""
SELECT (COUNT(DISTINCT ?cas) AS ?n) WHERE {{
  GRAPH {g('biobricks-toxcast')} {{ ?b {HASID} ?cas . }}
  GRAPH {g('biobricks-tox21')} {{ ?cas ?p ?o . }}
}}"""

Q["E2-cas"] = f"""
SELECT (COUNT(DISTINCT ?cas) AS ?n) WHERE {{
  GRAPH {g('biobricks-ice')} {{ ?b {HASID} ?cas . }}
  GRAPH {g('biobricks-tox21')} {{ ?cas ?p ?o . }}
}}"""

# --- Ensembl / Entrez gene cluster ----------------------------------------
Q["F2-ensembl-aopwiki"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('biobricks-aopwiki')} {{ ?s {EXACT} ?e . FILTER(STRSTARTS(STR(?e),'https://identifiers.org/ensembl/')) }}
  BIND(IRI(REPLACE(STR(?e),'https://identifiers.org/ensembl/','http://identifiers.org/ensembl/')) AS ?gene)
  GRAPH {g('gene-expression-atlas-okn')} {{ ?gene ?p ?o . }}
}}"""

Q["F1-ensembl-aopwiki"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('biobricks-aopwiki')} {{ ?s {EXACT} ?e . FILTER(STRSTARTS(STR(?e),'https://identifiers.org/ensembl/')) }}
  BIND(IRI(REPLACE(STR(?e),'https://identifiers.org/ensembl/','http://identifiers.org/ensembl/')) AS ?gene)
  GRAPH {g('spoke-okn')} {{ ?x ?p ?gene . }}
}}"""

Q["C8-entrez-aopwiki-rdkg"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('biobricks-aopwiki')} {{ ?s {EXACT} ?e . FILTER(STRSTARTS(STR(?e),'https://identifiers.org/ncbigene/')) }}
  BIND(IRI(REPLACE(STR(?e),'https://identifiers.org/ncbigene/','http://identifiers.org/ncbigene/')) AS ?gene)
  GRAPH {g('rdkg')} {{ ?gene ?p ?o . }}
}}"""

Q["C7-entrez-aopwiki-spokegenelab"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('biobricks-aopwiki')} {{ ?s {EXACT} ?e . FILTER(STRSTARTS(STR(?e),'https://identifiers.org/ncbigene/')) }}
  BIND(IRI(CONCAT('http://www.ncbi.nlm.nih.gov/gene/',REPLACE(STR(?e),'^.*/ncbigene/',''))) AS ?gene)
  GRAPH {g('spoke-genelab')} {{ ?gene ?p ?o . }}
}}"""

BL_GENE = "<https://w3id.org/biolink/vocab/Gene>"
Q["C1-ensembl"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('gene-expression-atlas-okn')} {{ ?gene a {BL_GENE} . }}
  {{ SELECT DISTINCT ?gene WHERE {{ GRAPH {g('spoke-okn')} {{ ?x ?q ?gene . FILTER(STRSTARTS(STR(?gene),'http://identifiers.org/ensembl/')) }} }} }}
}}"""

Q["C2-ensembl-3way"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('gene-expression-atlas-okn')} {{ ?gene a {BL_GENE} . }}
  {{ SELECT DISTINCT ?gene WHERE {{ GRAPH {g('spoke-okn')} {{ ?x ?q ?gene . FILTER(STRSTARTS(STR(?gene),'http://identifiers.org/ensembl/')) }} }} }}
  GRAPH {g('pankgraph')} {{ ?gene a {BL_GENE} . }}
}}"""

Q["C3-ensembl-disease"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('pankgraph')} {{ ?gene <https://w3id.org/biolink/vocab/gene_associated_with_condition> ?cond . FILTER(STRSTARTS(STR(?gene),'http://identifiers.org/ensembl/')) }}
  GRAPH {g('spoke-okn')} {{ ?x ?q ?gene . }}
}}"""

Q["C4-entrez-spoke"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('spoke-genelab')} {{ ?gene a {BL_GENE} . }}
  GRAPH {g('spoke-okn')} {{ ?gene a {BL_GENE} . }}
}}"""

Q["C5-entrez-rdkg-spokeokn"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('rdkg')} {{ ?r ?p ?o . FILTER(STRSTARTS(STR(?r),'http://identifiers.org/ncbigene/')) BIND(?r AS ?src) }}
  BIND(IRI(CONCAT('http://www.ncbi.nlm.nih.gov/gene/',REPLACE(STR(?src),'^.*/ncbigene/',''))) AS ?gene)
  GRAPH {g('spoke-okn')} {{ ?gene ?q ?r2 . }}
}}"""

Q["C6-entrez-rdkg-spokegenelab"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('rdkg')} {{ ?r ?p ?o . FILTER(STRSTARTS(STR(?r),'http://identifiers.org/ncbigene/')) BIND(?r AS ?src) }}
  BIND(IRI(CONCAT('http://www.ncbi.nlm.nih.gov/gene/',REPLACE(STR(?src),'^.*/ncbigene/',''))) AS ?gene)
  GRAPH {g('spoke-genelab')} {{ ?gene ?q ?r2 . }}
}}"""

# --- UniProt protein cluster ----------------------------------------------
Q["G1-uniprot-aopwiki-prokn"] = f"""
SELECT (COUNT(DISTINCT ?p2) AS ?n) WHERE {{
  GRAPH {g('biobricks-aopwiki')} {{ ?u ?pa ?oa . FILTER(STRSTARTS(STR(?u),'https://identifiers.org/uniprot/')) }}
  BIND(IRI(CONCAT('http://purl.uniprot.org/uniprot/',REPLACE(STR(?u),'^.*/uniprot/',''))) AS ?p2)
  GRAPH {g('prokn')} {{ ?p2 ?pp ?op . }}
}}"""

Q["G2-uniprot-ncipidkg-prokn"] = f"""
SELECT (COUNT(DISTINCT ?p2) AS ?n) WHERE {{
  GRAPH {g('ncipidkg')} {{ ?s {SAMEAS} ?u . FILTER(STRSTARTS(STR(?u),'http://identifiers.org/uniprot/')) }}
  BIND(IRI(CONCAT('http://purl.uniprot.org/uniprot/',REPLACE(STR(?u),'^.*/uniprot/',''))) AS ?p2)
  GRAPH {g('prokn')} {{ ?p2 ?pp ?op . }}
}}"""

# --- MeSH ------------------------------------------------------------------
# Driven from ubergraph's ~10k MESH dbxref CURIEs (bounded), rebuilding the
# biobricks-mesh node IRI and probing by bound subject. The reverse (scanning all
# biobricks-mesh triples) times out. Exact full intersection = 9883 (corrected the
# prior 9463, which was simply wrong, not data drift).
Q["M1-mesh-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?m) AS ?n) WHERE {{
  GRAPH {g('ubergraph')} {{ ?t {DBXREF} ?curie . FILTER(STRSTARTS(STR(?curie),'MESH:')) }}
  BIND(IRI(CONCAT('http://id.nlm.nih.gov/mesh/',REPLACE(STR(?curie),'^MESH:',''))) AS ?m)
  GRAPH {g('biobricks-mesh')} {{ ?m ?p ?o . }}
}}"""

# Drive from spoke-okn's two small MeSH predicates (mesh_list 155 + mesh_ids 10);
# a generic object-scan for the https mesh IRIs times out. Rebuild the http form
# and probe biobricks-mesh by bound subject.
_SPOKE_SCHEMA = "https://purl.org/okn/frink/kg/spoke-okn/schema/"
Q["M2-mesh-spokeokn"] = f"""
SELECT (COUNT(DISTINCT ?id) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?s ?p ?mo .
    VALUES ?p {{ <{_SPOKE_SCHEMA}mesh_list> <{_SPOKE_SCHEMA}mesh_ids> }} }}
  BIND(REPLACE(STR(?mo),'^https://id.nlm.nih.gov/mesh/','') AS ?id)
  BIND(IRI(CONCAT('http://id.nlm.nih.gov/mesh/',?id)) AS ?m)
  GRAPH {g('biobricks-mesh')} {{ ?m ?bp ?bo . }}
}}"""

# --- PubChem CID -----------------------------------------------------------
Q["P1-pubchem-cid-biobricks-spokeokn"] = f"""
SELECT (COUNT(DISTINCT ?cid) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?c {DBXREF} ?x . FILTER(STRSTARTS(STR(?x),'http://identifiers.org/pubchem.compound/')) }}
  BIND(REPLACE(STR(?x),'^http://identifiers.org/pubchem.compound/','') AS ?cid)
  BIND(IRI(CONCAT('http://rdf.ncbi.nlm.nih.gov/pubchem/compound/CID',?cid)) AS ?b)
  GRAPH {g('biobricks-pubchem-annotations')} {{ ?ann <http://www.w3.org/ns/oa#hasTarget> ?b . }}
}}"""

# --- Disease ontology cluster (DOID / MONDO / HP) --------------------------
# oard-kg is a biolink-reified KG: a disease/phenotype IRI can sit on EITHER side
# of an association, so it appears as the object of BOTH biolink:object and
# biolink:subject. Join on only one position and the recipe undercounts (the
# silent partial-result failure). UNION both so the join is entity-complete.
Q["A1-hp"] = f"""
SELECT (COUNT(DISTINCT ?hp) AS ?n) WHERE {{
  GRAPH {g('oard-kg')} {{ {{ ?s {BL_OBJ} ?hp }} UNION {{ ?ss {BL_SUBJ} ?hp }} FILTER(STRSTARTS(STR(?hp),'http://purl.obolibrary.org/obo/HP_')) }}
  GRAPH {g('prokn')} {{ ?x {SEEALSO} ?hp . }}
}}"""

Q["A3-mondo"] = f"""
SELECT (COUNT(DISTINCT ?mondo) AS ?n) WHERE {{
  GRAPH {g('oard-kg')} {{ {{ ?s {BL_OBJ} ?mondo }} UNION {{ ?ss {BL_SUBJ} ?mondo }} FILTER(STRSTARTS(STR(?mondo),'http://purl.obolibrary.org/obo/MONDO_')) }}
  GRAPH {g('prokn')} {{ ?x a {UP_DISEASE} ; {SEEALSO} ?mondo . }}
}}"""

Q["A4-mondo"] = f"""
SELECT (COUNT(DISTINCT ?mondo) AS ?n) WHERE {{
  GRAPH {g('nde')} {{ ?s <http://schema.org/healthCondition> ?mondo . FILTER(STRSTARTS(STR(?mondo),'http://purl.obolibrary.org/obo/MONDO_')) }}
  GRAPH {g('oard-kg')} {{ {{ ?x {BL_OBJ} ?mondo }} UNION {{ ?xs {BL_SUBJ} ?mondo }} }}
}}"""

Q["A2-mondo"] = f"""
SELECT (COUNT(DISTINCT ?mondo) AS ?n) WHERE {{
  GRAPH {g('rdkg')} {{ ?mondo ?p ?o . FILTER(STRSTARTS(STR(?mondo),'http://purl.obolibrary.org/obo/MONDO_')) }}
  GRAPH {g('oard-kg')} {{ {{ ?x {BL_OBJ} ?mondo }} UNION {{ ?xs {BL_SUBJ} ?mondo }} }}
}}"""

Q["A5-doid"] = f"""
SELECT (COUNT(DISTINCT ?doid) AS ?n) WHERE {{
  GRAPH {g('prokn')} {{ ?x {SEEALSO} ?doid . FILTER(STRSTARTS(STR(?doid),'http://purl.obolibrary.org/obo/DOID_')) }}
  GRAPH {g('biomarkerkg')} {{ ?b ?p ?doid . }}
}}"""

Q["A6-mondo-expansion"] = f"""
SELECT (COUNT(DISTINCT ?disease) AS ?n) WHERE {{
  GRAPH {g('ubergraph')} {{ ?disease {SUBCLASS}* <http://purl.obolibrary.org/obo/MONDO_0004995> . }}
  GRAPH {g('oard-kg')} {{ {{ ?x {BL_SUBJ} ?disease }} UNION {{ ?xo {BL_OBJ} ?disease }} }}
}}"""

Q["A7-doid-spokeokn-prokn"] = f"""
SELECT (COUNT(DISTINCT ?doid) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?doid a <https://w3id.org/biolink/vocab/Disease> . FILTER(STRSTARTS(STR(?doid),'http://purl.obolibrary.org/obo/DOID_')) }}
  GRAPH {g('prokn')} {{ ?x ?q ?doid . }}
}}"""

Q["A8-doid-spokeokn-biomarkerkg"] = f"""
SELECT (COUNT(DISTINCT ?doid) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?doid a <https://w3id.org/biolink/vocab/Disease> . FILTER(STRSTARTS(STR(?doid),'http://purl.obolibrary.org/obo/DOID_')) }}
  GRAPH {g('biomarkerkg')} {{ ?x ?q ?doid . }}
}}"""

Q["A9-doid-spokeokn-nde"] = f"""
SELECT (COUNT(DISTINCT ?doid) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?doid a <https://w3id.org/biolink/vocab/Disease> . FILTER(STRSTARTS(STR(?doid),'http://purl.obolibrary.org/obo/DOID_')) }}
  GRAPH {g('nde')} {{ ?x <http://schema.org/healthCondition> ?doid . }}
}}"""

Q["A10-doid-mondo-spokeokn-rdkg"] = f"""
SELECT (COUNT(DISTINCT ?doid) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?doid a <https://w3id.org/biolink/vocab/Disease> . FILTER(STRSTARTS(STR(?doid),'http://purl.obolibrary.org/obo/DOID_')) }}
  GRAPH {g('ubergraph')} {{ ?mondo {EXACT} ?doid . }}
  GRAPH {g('rdkg')} {{ ?x ?q ?mondo . }}
}}"""

Q["A11-doid-mondo-spokeokn-oardkg"] = f"""
SELECT (COUNT(DISTINCT ?doid) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?doid a <https://w3id.org/biolink/vocab/Disease> . FILTER(STRSTARTS(STR(?doid),'http://purl.obolibrary.org/obo/DOID_')) }}
  GRAPH {g('ubergraph')} {{ ?mondo {EXACT} ?doid . }}
  GRAPH {g('oard-kg')} {{ {{ ?x {BL_OBJ} ?mondo }} UNION {{ ?xs {BL_SUBJ} ?mondo }} }}
}}"""

# oard-kg MONDO -> ubergraph hasDbXref 'OMIM:{{id}}' -> prokn OMIM seeAlso.
# prokn stores OMIM as https://www.omim.org/entry/{{id}} (https, www); rebuild it
# from the bare id in ubergraph's OMIM CURIE.
Q["A12-omim-oardkg-prokn-via-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?mondo) AS ?n) WHERE {{
  GRAPH {g('oard-kg')} {{ {{ ?x {BL_OBJ} ?mondo }} UNION {{ ?xs {BL_SUBJ} ?mondo }} FILTER(STRSTARTS(STR(?mondo),'http://purl.obolibrary.org/obo/MONDO_')) }}
  GRAPH {g('ubergraph')} {{ ?mondo {DBXREF} ?curie . FILTER(STRSTARTS(STR(?curie),'OMIM:')) }}
  BIND(IRI(CONCAT('https://www.omim.org/entry/',REPLACE(STR(?curie),'^OMIM:',''))) AS ?omim)
  GRAPH {g('prokn')} {{ ?y a {UP_DISEASE} ; {SEEALSO} ?omim . }}
}}"""

# oard-kg MONDO -> ubergraph hasDbXref 'Orphanet:{{id}}' -> prokn up:Disease Orphanet
# seeAlso (http://www.orpha.net/ORDO/Orphanet_{{id}}, 2,192 such ids on up:Disease).
# Sibling of A12: recovers prokn diseases keyed by Orphanet but no usable MONDO/OMIM.
Q["A14-orphanet-oardkg-prokn-via-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?mondo) AS ?n) WHERE {{
  GRAPH {g('oard-kg')} {{ {{ ?x {BL_OBJ} ?mondo }} UNION {{ ?xs {BL_SUBJ} ?mondo }} FILTER(STRSTARTS(STR(?mondo),'http://purl.obolibrary.org/obo/MONDO_')) }}
  GRAPH {g('ubergraph')} {{ ?mondo {DBXREF} ?curie . FILTER(STRSTARTS(STR(?curie),'Orphanet:')) }}
  BIND(IRI(CONCAT('http://www.orpha.net/ORDO/Orphanet_',REPLACE(STR(?curie),'^Orphanet:',''))) AS ?orpha)
  GRAPH {g('prokn')} {{ ?y a {UP_DISEASE} ; {SEEALSO} ?orpha . }}
}}"""

# oard-kg MONDO -> ubergraph skos:exactMatch DOID -> prokn up:Disease DOID seeAlso.
# Sibling of A12; reaches 111 disease entities (109 net-new) but those net-new
# DOID-only diseases carry NO up:Protein associations (0 net-new disease-protein pairs).
Q["A15-doid-oardkg-prokn-via-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?mondo) AS ?n) WHERE {{
  GRAPH {g('oard-kg')} {{ {{ ?x {BL_OBJ} ?mondo }} UNION {{ ?xs {BL_SUBJ} ?mondo }} FILTER(STRSTARTS(STR(?mondo),'http://purl.obolibrary.org/obo/MONDO_')) }}
  GRAPH {g('ubergraph')} {{ ?mondo {EXACT} ?doid . FILTER(STRSTARTS(STR(?doid),'http://purl.obolibrary.org/obo/DOID_')) }}
  GRAPH {g('prokn')} {{ ?y a {UP_DISEASE} ; {SEEALSO} ?doid . }}
}}"""

# --- CHEBI<->CAS bridge ----------------------------------------------------
Q["B2-chebi-cas-bridge"] = f"""
SELECT (COUNT(DISTINCT ?c2) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?cmp {DBXREF} ?chebi . FILTER(STRSTARTS(STR(?chebi),'http://purl.obolibrary.org/obo/CHEBI_')) }}
  GRAPH {g('ubergraph')} {{ ?chebi {DBXREF} ?casCurie . FILTER(STRSTARTS(STR(?casCurie),'cas:')) }}
  BIND(IRI(CONCAT('http://identifiers.org/cas/',SUBSTR(STR(?casCurie),5))) AS ?c2)
  GRAPH {g('biobricks-toxcast')} {{ ?t {HASID} ?c2 . }}
}}"""

# --- HGNC -> Entrez via Wikidata ------------------------------------------
# Driven from spoke-okn's bounded biolink:Gene set (Entrez node IRIs), bridged
# through Wikidata (P351 Entrez -> P354 HGNC), then prokn probed by bound HGNC
# IRI. The natural prokn->wikidata->spoke direction times out: prokn's HGNC ids
# are spread across many predicates, so the object-scan is unbounded.
Q["C9-hgnc-prokn-via-wikidata"] = f"""
SELECT (COUNT(DISTINCT ?gene) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?gene a <https://w3id.org/biolink/vocab/Gene> . }}
  BIND(REPLACE(STR(?gene),'^.*/gene/','') AS ?entrez)
  GRAPH {g('wikidata')} {{ ?item <http://www.wikidata.org/prop/direct/P351> ?entrez ;
                                  <http://www.wikidata.org/prop/direct/P354> ?hgnc . }}
  BIND(IRI(CONCAT('http://identifiers.org/hgnc/',?hgnc)) AS ?h)
  GRAPH {g('prokn')} {{ ?x ?p ?h . }}
}}"""

# --- S2 spatial cells ------------------------------------------------------
S2PFX = "http://stko-kwg.geog.ucsb.edu/lod/resource/s2.level13."
Q["N1-s2-fiokg-spatialkg"] = f"""
SELECT (COUNT(DISTINCT ?cell) AS ?n) WHERE {{
  GRAPH {g('fiokg')} {{ ?f {SAMEAS} ?cell . FILTER(STRSTARTS(STR(?cell),'{S2PFX}')) }}
  GRAPH {g('spatialkg')} {{ ?cell a <http://stko-kwg.geog.ucsb.edu/lod/ontology/S2Cell_Level13> . }}
}}"""

Q["N6-s2-fiokg-sawgraph"] = f"""
SELECT (COUNT(DISTINCT ?cell) AS ?n) WHERE {{
  GRAPH {g('fiokg')} {{ ?f {SAMEAS} ?cell . FILTER(STRSTARTS(STR(?cell),'{S2PFX}')) }}
  GRAPH {g('sawgraph')} {{ ?s {SAMEAS} ?cell . }}
}}"""

Q["D1-s2"] = f"""
SELECT (COUNT(DISTINCT ?cell) AS ?n) WHERE {{
  GRAPH {g('hydrologykg')} {{ ?h <http://stko-kwg.geog.ucsb.edu/lod/ontology/sfWithin> ?cell . FILTER(STRSTARTS(STR(?cell),'{S2PFX}')) }}
  GRAPH {g('spatialkg')} {{ ?cell a <http://stko-kwg.geog.ucsb.edu/lod/ontology/S2Cell_Level13> . }}
}}"""

Q["D2-s2"] = f"""
SELECT (COUNT(DISTINCT ?cell) AS ?n) WHERE {{
  GRAPH {g('sawgraph')} {{ ?s {SAMEAS} ?cell . FILTER(STRSTARTS(STR(?cell),'{S2PFX}')) }}
  GRAPH {g('spatialkg')} {{ ?cell a <http://stko-kwg.geog.ucsb.edu/lod/ontology/S2Cell_Level13> . }}
}}"""

Q["H1-s2"] = f"""
SELECT (COUNT(DISTINCT ?cell) AS ?n) WHERE {{
  GRAPH {g('sockg')} {{ ?cell a <http://stko-kwg.geog.ucsb.edu/lod/ontology/S2Cell_Level13> . }}
  GRAPH {g('spatialkg')} {{ ?cell a <http://stko-kwg.geog.ucsb.edu/lod/ontology/S2Cell_Level13> . }}
}}"""

# ufokn stores https://schema.org/value+name; the server rewrites a query's
# https://schema.org/ to http://, so we cannot name the predicate IRI directly.
# Match the distinctive "s2Level13" literal via the object index, then read the
# sibling value predicate by string (CONTAINS, scheme-agnostic).
Q["O1-s2-ufokn-spatialkg"] = f"""
SELECT (COUNT(DISTINCT ?cell) AS ?n) WHERE {{
  {{ SELECT DISTINCT ?cell WHERE {{
    GRAPH {g('ufokn')} {{
      ?bn ?pn "s2Level13" .
      ?bn ?pv ?s2id .
      FILTER(CONTAINS(STR(?pv),'schema.org/value'))
    }}
    BIND(IRI(CONCAT('{S2PFX}',STR(?s2id))) AS ?cell)
  }} }}
  GRAPH {g('spatialkg')} {{ ?cell a <http://stko-kwg.geog.ucsb.edu/lod/ontology/S2Cell_Level13> . }}
}}"""

# --- County / state FIPS ---------------------------------------------------
KWGADMIN = "http://stko-kwg.geog.ucsb.edu/lod/resource/administrativeRegion.USA."
Q["N2-county-fips-fiokg-spatialkg"] = f"""
SELECT (COUNT(DISTINCT ?reg) AS ?n) WHERE {{
  GRAPH {g('fiokg')} {{ ?f {SAMEAS} ?reg . FILTER(STRSTARTS(STR(?reg),'{KWGADMIN}')) }}
  FILTER(STRLEN(REPLACE(STR(?reg),'^.*administrativeRegion[.]USA[.]',''))=5)
  GRAPH {g('spatialkg')} {{ ?reg a <http://stko-kwg.geog.ucsb.edu/lod/ontology/AdministrativeRegion_2> . }}
}}"""

Q["N3-county-fips-fiokg-spokeokn"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('fiokg')} {{ ?f {SAMEAS} ?reg . FILTER(STRSTARTS(STR(?reg),'{KWGADMIN}')) }}
  BIND(REPLACE(STR(?reg),'^.*administrativeRegion\\\\.USA\\\\.','') AS ?fips)
  FILTER(STRLEN(?fips)=5)
  BIND(IRI(CONCAT('https://purl.org/okn/frink/kg/spoke-okn/location/',?fips)) AS ?loc)
  GRAPH {g('spoke-okn')} {{ ?loc ?p ?o . }}
}}"""

Q["L1-county-fips-geoconnex-spatialkg"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('geoconnex')} {{ ?x <http://gnis-ld.org/lod/gnis/ontology/county> ?county . }}
  BIND(REPLACE(STR(?county),'^.*/counties/([0-9]{{5}}).*$','$1') AS ?fips)
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('spatialkg')} {{ ?reg a <http://stko-kwg.geog.ucsb.edu/lod/ontology/AdministrativeRegion_2> . }}
}}"""

Q["L2-county-fips-geoconnex-spokeokn"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('geoconnex')} {{ ?x <http://gnis-ld.org/lod/gnis/ontology/county> ?county . }}
  BIND(REPLACE(STR(?county),'^.*/counties/([0-9]{{5}}).*$','$1') AS ?fips)
  BIND(IRI(CONCAT('https://purl.org/okn/frink/kg/spoke-okn/location/',?fips)) AS ?loc)
  GRAPH {g('spoke-okn')} {{ ?loc ?p ?o . }}
}}"""

Q["L3-state-fips-geoconnex-spatialkg"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('geoconnex')} {{ ?x <http://gnis-ld.org/lod/gnis/ontology/state> ?st . }}
  BIND(REPLACE(STR(?st),'^.*/states/([0-9]{{2}}).*$','$1') AS ?fips)
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('spatialkg')} {{ ?reg a <http://stko-kwg.geog.ucsb.edu/lod/ontology/AdministrativeRegion_1> . }}
}}"""

Q["L4-county-fips-scales-spatialkg"] = f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('scales')} {{ ?x <http://schemas.scales-okn.org/rdf/scales#hasIdbCounty> ?c . FILTER(?c != 88888) }}
  BIND(REPLACE(CONCAT('00000',STR(xsd:integer(?c))),'^.*(.{{5}})$','$1') AS ?fips)
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('spatialkg')} {{ ?reg a <http://stko-kwg.geog.ucsb.edu/lod/ontology/AdministrativeRegion_2> . }}
}}"""

Q["L5-county-fips-scales-spokeokn"] = f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('scales')} {{ ?x <http://schemas.scales-okn.org/rdf/scales#hasIdbCounty> ?c . FILTER(?c != 88888) }}
  BIND(REPLACE(CONCAT('00000',STR(xsd:integer(?c))),'^.*(.{{5}})$','$1') AS ?fips)
  BIND(IRI(CONCAT('https://purl.org/okn/frink/kg/spoke-okn/location/',?fips)) AS ?loc)
  GRAPH {g('spoke-okn')} {{ ?loc ?p ?o . }}
}}"""

Q["L6-county-fips-nikg-spatialkg"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('nikg')} {{ ?x <http://stko-kwg.geog.ucsb.edu/lod/ontology/sfWithin> ?o . FILTER(STRSTARTS(STR(?o),'https://metadata.phila.gov/kwgr_administrativeRegion_USA_')) }}
  BIND(REPLACE(STR(?o),'^.*administrativeRegion_USA_([0-9]{{5}}).*$','$1') AS ?fips)
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('spatialkg')} {{ ?reg ?p ?o2 . }}
}}"""

Q["L7-county-fips-nikg-spokeokn"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('nikg')} {{ ?x <http://stko-kwg.geog.ucsb.edu/lod/ontology/sfWithin> ?o . FILTER(STRSTARTS(STR(?o),'https://metadata.phila.gov/kwgr_administrativeRegion_USA_')) }}
  BIND(REPLACE(STR(?o),'^.*administrativeRegion_USA_([0-9]{{5}}).*$','$1') AS ?fips)
  BIND(IRI(CONCAT('https://purl.org/okn/frink/kg/spoke-okn/location/',?fips)) AS ?loc)
  GRAPH {g('spoke-okn')} {{ ?loc ?p ?o2 . }}
}}"""

# Match the rebuilt KWG county IRI by EXISTENCE (`a ?t`, any type), not the
# AdministrativeRegion_2 type specifically: 27 of the joined counties are typed
# as something other than AR_2 in spatialkg, so a strict AR_2 filter under-counts
# (3,095 vs 3,122). Collapse the spoke side to DISTINCT FIPS first to bound it.
Q["K1-county-fips"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  {{ SELECT DISTINCT ?fips WHERE {{ GRAPH {g('spoke-okn')} {{ ?loc ?p ?o . FILTER(REGEX(STR(?loc),'/location/[0-9]{{5}}$')) }} BIND(REPLACE(STR(?loc),'^.*/location/([0-9]{{5}})$','$1') AS ?fips) }} }}
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('spatialkg')} {{ ?reg a ?t . }}
}}"""

Q["K2-county-fips"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?loc ?p ?o . FILTER(REGEX(STR(?loc),'/location/[0-9]{{5}}$')) }}
  BIND(REPLACE(STR(?loc),'^.*/location/([0-9]{{5}})$','$1') AS ?fips)
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('ruralkg')} {{ ?x <http://sail.ua.edu/ruralkg/settlementtype/censusCounty> ?reg . }}
}}"""

Q["K3-county-fips"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?loc ?p ?o . FILTER(REGEX(STR(?loc),'/location/[0-9]{{5}}$')) }}
  BIND(REPLACE(STR(?loc),'^.*/location/([0-9]{{5}})$','$1') AS ?fips)
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('sockg')} {{ ?reg ?q ?r . }}
}}"""

Q["K4-state-fips"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?loc ?p ?o . FILTER(REGEX(STR(?loc),'/location/[0-9]{{2}}$')) }}
  BIND(REPLACE(STR(?loc),'^.*/location/([0-9]{{2}})$','$1') AS ?fips)
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('spatialkg')} {{ ?reg a <http://stko-kwg.geog.ucsb.edu/lod/ontology/AdministrativeRegion_1> . }}
}}"""

Q["K5-state-fips"] = f"""
SELECT (COUNT(DISTINCT ?fips) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?loc ?p ?o . FILTER(REGEX(STR(?loc),'/location/[0-9]{{2}}$')) }}
  BIND(REPLACE(STR(?loc),'^.*/location/([0-9]{{2}})$','$1') AS ?fips)
  BIND(IRI(CONCAT('{KWGADMIN}',?fips)) AS ?reg)
  GRAPH {g('sockg')} {{ ?reg ?q ?r . }}
}}"""

Q["H2-county"] = f"""
SELECT (COUNT(DISTINCT ?reg) AS ?n) WHERE {{
  GRAPH {g('sockg')} {{ ?reg a <http://stko-kwg.geog.ucsb.edu/lod/ontology/AdministrativeRegion_2> . }}
  GRAPH {g('spatialkg')} {{ ?reg a <http://stko-kwg.geog.ucsb.edu/lod/ontology/AdministrativeRegion_2> . }}
}}"""

# --- NAICS / industry sector ----------------------------------------------
SUDOKN = "http://asu.edu/semantics/SUDOKN/"
SCK_NAICS = f"{{ SELECT DISTINCT ?code WHERE {{ GRAPH {g('securechainkg')} {{ ?a <{SUDOKN}hasPrimaryNAICSClassifier> ?sn . }} BIND(REPLACE(STR(?sn),'^.*/naics-([0-9]+)\\\\.0-inst$','$1') AS ?code) }} }}"
SUD_NAICS = f"{{ SELECT DISTINCT ?code WHERE {{ GRAPH {g('sudokn')} {{ ?b <{SUDOKN}hasPrimaryNAICSClassifier> ?dn . }} BIND(REPLACE(STR(?dn),'^.*NAICS%20([0-9]+)-individual$','$1') AS ?code) }} }}"
FIO_NAICS = f"{{ SELECT DISTINCT ?code WHERE {{ GRAPH {g('fiokg')} {{ ?f <http://w3id.org/fio/v1/epa-frs#ofPrimaryIndustry>|<http://w3id.org/fio/v1/epa-frs#ofSecondaryIndustry> ?ind . }} BIND(REPLACE(STR(?ind),'^.*naics#NAICS-([0-9]+)$','$1') AS ?code) }} }}"
Q["I1-naics"] = f"""
SELECT (COUNT(DISTINCT ?code) AS ?n) WHERE {{
  {SCK_NAICS}
  {SUD_NAICS}
}}"""

Q["N4-naics-fiokg-sudokn"] = f"""
SELECT (COUNT(DISTINCT ?code) AS ?n) WHERE {{
  {FIO_NAICS}
  {SUD_NAICS}
}}"""

Q["N5-naics-fiokg-securechainkg"] = f"""
SELECT (COUNT(DISTINCT ?code) AS ?n) WHERE {{
  {FIO_NAICS}
  {SCK_NAICS}
}}"""

Q["I2-industry-sector"] = f"""
SELECT (COUNT(DISTINCT ?sec) AS ?n) WHERE {{
  {{ SELECT DISTINCT ?sec WHERE {{ GRAPH {g('securechainkg')} {{ ?a <{SUDOKN}suppliesToIndustry> ?si . }} BIND(LCASE(REPLACE(STR(?si),'^.*/SUDOKN/(.*)-inst$','$1')) AS ?sec) }} }}
  {{ SELECT DISTINCT ?sec WHERE {{ GRAPH {g('sudokn')} {{ ?b <{SUDOKN}suppliesToIndustry> ?di . }} BIND(LCASE(REPLACE(REPLACE(STR(?di),'^.*/SUDOKN/(.*)-industry-individual$','$1'),'%20','')) AS ?sec) }} }}
}}"""

# --- ZIP literals ----------------------------------------------------------
Q["J1-zip"] = f"""
SELECT (COUNT(DISTINCT ?zip) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?loc {LABEL} ?zip . FILTER(REGEX(STR(?loc),'/location/[A-Z]{{2}}-[0-9]+')) }}
  GRAPH {g('sudokn')} {{ ?b <{SUDOKN}hasZipcodeValue> ?zip . }}
}}"""

Q["J2-zip"] = f"""
SELECT (COUNT(DISTINCT ?zip) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?loc {LABEL} ?zip . FILTER(REGEX(STR(?loc),'/location/[A-Z]{{2}}-[0-9]+')) }}
  GRAPH {g('ruralkg')} {{ ?b ?p ?zip . FILTER(CONTAINS(STR(?p),'postalCode')) }}
}}"""

Q["J3-zip"] = f"""
SELECT (COUNT(DISTINCT ?zip) AS ?n) WHERE {{
  GRAPH {g('spoke-okn')} {{ ?loc {LABEL} ?zip . FILTER(REGEX(STR(?loc),'/location/[A-Z]{{2}}-[0-9]+')) }}
  GRAPH {g('dreamkg')} {{ ?b <http://schema.org/postalCode> ?zip . }}
}}"""

# --- NCBITaxon expansion ---------------------------------------------------
# Shared-key cardinality: sawgraph's NCBITaxon terms (subjects of subClassOf)
# that exist in ubergraph's taxonomy — all 538 do. (The recipe's clade expansion
# `?taxon subClassOf* <parent>` is the example USE for category queries, not the
# join's size; restricting to an arbitrary clade under-counts.)
Q["D3-ncbitaxon"] = f"""
SELECT (COUNT(DISTINCT ?taxon) AS ?n) WHERE {{
  GRAPH {g('sawgraph')} {{ ?taxon {SUBCLASS} ?sup . FILTER(STRSTARTS(STR(?taxon),'http://purl.obolibrary.org/obo/NCBITaxon_')) }}
  GRAPH {g('ubergraph')} {{ ?taxon {SUBCLASS} ?u . }}
}}"""

# spoke-genelab stores each gene's organism as a STRING literal NCBITaxon IRI on
# the Gene.taxonomy node property (not as a node); coerce STR->IRI before joining
# ubergraph's taxonomy. All 9 model-organism taxa overlap (9/9). The microbial
# Organism class (node/N IRIs) is label-only — no NCBITaxon id — so it cannot join.
_SGL = "https://purl.org/okn/frink/kg/spoke-genelab/schema/"
Q["D4-ncbitaxon-spokegenelab-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?taxon) AS ?n) WHERE {{
  GRAPH {g('spoke-genelab')} {{ ?gene <{_SGL}taxonomy> ?ts . FILTER(STRSTARTS(STR(?ts),'http://purl.obolibrary.org/obo/NCBITaxon_')) }}
  BIND(IRI(STR(?ts)) AS ?taxon)
  GRAPH {g('ubergraph')} {{ ?taxon {SUBCLASS} ?u . }}
}}"""

# spoke-okn's OrganismTaxon nodes (bacterial strains + other organisms) are PATRIC/
# BV-BRC genome IRIs of the form .../organism/{ncbi_taxon_id}.{assembly}. Extract the
# integer taxon-id prefix ([0-9]+ stops at the dot, no escaping needed) and rebuild
# obo/NCBITaxon_{id}, then join ubergraph's taxonomy. 33,602 of 34,570 distinct taxa
# overlap. Collapse to DISTINCT taxa first (321k genome nodes -> 34.5k taxa) to bound it.
Q["D5-ncbitaxon-spokeokn-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?taxon) AS ?n) WHERE {{
  {{ SELECT DISTINCT ?taxon WHERE {{
    GRAPH {g('spoke-okn')} {{ ?o a <https://w3id.org/biolink/vocab/OrganismTaxon> . }}
    BIND(IRI(CONCAT('http://purl.obolibrary.org/obo/NCBITaxon_',REPLACE(STR(?o),'^.*/organism/([0-9]+).*$','$1'))) AS ?taxon)
  }} }}
  GRAPH {g('ubergraph')} {{ ?taxon {SUBCLASS} ?u . }}
}}"""

# gene-expression-atlas-okn carries obo/NCBITaxon_ directly as the object of
# biolink:in_taxon (8 model organisms); join straight to ubergraph.
Q["D6-ncbitaxon-gxa-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?taxon) AS ?n) WHERE {{
  GRAPH {g('gene-expression-atlas-okn')} {{ ?s <https://w3id.org/biolink/vocab/in_taxon> ?taxon . FILTER(STRSTARTS(STR(?taxon),'http://purl.obolibrary.org/obo/NCBITaxon_')) }}
  GRAPH {g('ubergraph')} {{ ?taxon {SUBCLASS} ?u . }}
}}"""

# biobricks-aopwiki references each AOP's taxonomic applicability as obo/NCBITaxon_
# on dc:identifier (the bounded, semantically-correct predicate); 164 join ubergraph.
Q["D7-ncbitaxon-aopwiki-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?taxon) AS ?n) WHERE {{
  GRAPH {g('biobricks-aopwiki')} {{ ?s <http://purl.org/dc/elements/1.1/identifier> ?taxon . FILTER(STRSTARTS(STR(?taxon),'http://purl.obolibrary.org/obo/NCBITaxon_')) }}
  GRAPH {g('ubergraph')} {{ ?taxon {SUBCLASS} ?u . }}
}}"""

# nde stores species as https://www.uniprot.org/taxonomy/{id} (the id is the NCBI
# taxon id) on schema:species; extract the id and rebuild obo/NCBITaxon_{id}. 1,797
# of 1,808 join ubergraph. Collapse to DISTINCT taxa first.
Q["D8-ncbitaxon-nde-ubergraph"] = f"""
SELECT (COUNT(DISTINCT ?taxon) AS ?n) WHERE {{
  {{ SELECT DISTINCT ?taxon WHERE {{
    GRAPH {g('nde')} {{ ?s <http://schema.org/species> ?o . FILTER(CONTAINS(STR(?o),'/taxonomy/')) }}
    BIND(IRI(CONCAT('http://purl.obolibrary.org/obo/NCBITaxon_',REPLACE(STR(?o),'^.*/taxonomy/([0-9]+).*$','$1'))) AS ?taxon)
  }} }}
  GRAPH {g('ubergraph')} {{ ?taxon {SUBCLASS} ?u . }}
}}"""


RESULTS = ROOT / "scripts" / ".skeleton_results.json"


def _load_results() -> dict[str, dict]:
    if RESULTS.exists():
        return json.loads(RESULTS.read_text())
    return {}


async def main() -> None:
    inject = "--inject" in sys.argv
    summary = "--summary" in sys.argv
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")]
    data = json.loads(SRC.read_text())
    by_id = {e["id"]: e for e in data["verified_crosswalks"]}
    results = _load_results()

    if inject:
        # Inject a runnable skeleton for every entry whose query returned rows.
        # Exact reproductions are flagged ``skeleton_verified: true``; near-misses
        # also carry ``skeleton_returns`` so drift vs ``verified_count`` is visible.
        exact = near = 0
        for cid, rec in results.items():
            if cid not in by_id or not rec.get("got"):
                continue
            entry = by_id[cid]
            entry["skeleton_query"] = rec["query"].strip()
            if rec.get("verified"):
                entry["skeleton_verified"] = True
                entry.pop("skeleton_returns", None)  # exact: no drift to disclose
                exact += 1
            else:
                entry["skeleton_verified"] = False
                entry["skeleton_returns"] = rec["got"]
                near += 1
        SRC.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"injected {exact} exact + {near} near skeleton_query fields into {SRC}")
        return

    if summary:
        ok = [c for c, r in results.items() if r.get("verified")]
        near = [c for c, r in results.items() if r.get("got") and not r.get("verified")]
        bad = [c for c, r in results.items() if not r.get("got")]
        missing = [e["id"] for e in data["verified_crosswalks"] if e["id"] not in results]
        print(f"verified(exact)={len(ok)}  near={len(near)}  zero/err={len(bad)}  untested={len(missing)}")
        print("near :", near)
        print("bad  :", bad)
        print("untested:", missing)
        return

    ids = wanted or [e["id"] for e in data["verified_crosswalks"]]
    async with httpx.AsyncClient(timeout=90.0) as client:
        for cid in ids:
            q = Q.get(cid)
            entry = by_id.get(cid)
            if q is None:
                print(f"  --   {cid}: NO SKELETON AUTHORED")
                continue
            expected = entry.get("verified_count") if entry else None
            try:
                r = await run_sparql(q, timeout=90.0, client=client)
                got = r["rows"][0]["n"] if r["rows"] else 0
                verified = got == expected
                status = "OK " if verified else ("~  " if got else "ZERO")
                print(f"  {status} {cid}: got={got} expected={expected}")
                results[cid] = {"query": q, "got": got, "expected": expected, "verified": verified}
            except Exception as exc:  # noqa: BLE001
                print(f"  ERR  {cid}: {str(exc)[:120]}")
                results[cid] = {"query": q, "got": None, "expected": expected, "verified": False}
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nwrote results for {len(results)} entries to {RESULTS}")


if __name__ == "__main__":
    asyncio.run(main())
