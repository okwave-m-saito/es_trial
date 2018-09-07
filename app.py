#!/usr/local/python

import os
import json
import urllib
import time

from os.path import join, dirname
from xml.etree import ElementTree

import requests

from dotenv import load_dotenv
from flask import Flask, render_template, request
from sshtunnel import SSHTunnelForwarder

dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)

SSH_PRIVATE_KEY = os.environ.get('SSH_PRIVATE_KEY')
SSH_USERNAME = os.environ.get('SSH_USERNAME')
SSH_PRIVATE_KEY_PASSWORD = os.environ.get('SSH_PRIVATE_KEY_PASSWORD')

app = Flask(__name__)


# 踏み台サーバーに接続する（使い終わったらserver.stop()すること）
def connect(host, port):
    server = SSHTunnelForwarder(
        ('entra.answers.okwave.jp', 2022),
        ssh_private_key=SSH_PRIVATE_KEY,
        ssh_username=SSH_USERNAME,
        ssh_private_key_password=SSH_PRIVATE_KEY_PASSWORD,
        remote_bind_address=(host, port)
    )
    server.start()
    return server


# Elasticsearchのインデックスを検索する
def search_es(index, query):
    server = connect('search01.answers-tmp', 9200)
    url = 'http://localhost:' + str(server.local_bind_port) + '/' + index + '/_search'
    payload = {
        'profile': True,
        'size': 10,
        'query': {
            'bool': {
                'should': [
                    {
                        'bool': {
                            'must': {
                                'multi_match': {
                                    'type': 'best_fields',
                                    'query': query,
                                    'fields': ['contents_title^2', 'contents_text'],
                                    'operator': 'and'
                                }
                            },
                            'filter': { 'term': { 'type': 'questions' } }
                        }
                    },
                    {
                        'has_child': {
                            'type': 'answers',
                            'query': {
                                'match': {
                                    'contents_text': {
                                        'query': query,
                                        'operator': 'and'
                                    }
                                }
                            }
                        }
                    }
                ]
            }
        }
    }
    headers = {'content-type': 'application/json'}
    start = time.time()
    r = requests.post(url, data=json.dumps(payload), headers=headers)
    elapsed_time = time.time() - start
    total = r.json()['hits']['total']
    hits = r.json()['hits']['hits']
    ids_tmp = []
    for row in hits:
        id = ''
        if row['_source']['type'] == 'questions':
            id = row['_id']
        ids_tmp.append(id)
    # 回答も検索しているため、順番を保持した状態で重複したQIDを削除する
    ids = sorted(set(ids_tmp), key=ids_tmp.index)
    rows = []
    for id in ids:
        url = 'http://localhost:' + str(server.local_bind_port) + '/' + index + '/_doc/' + id
        r = requests.get(url)
        if r.json()['found']:
            title = r.json()['_source']['contents_title'][:60]
            text = r.json()['_source']['contents_text'][:60]
            rows.append({'id': id.replace('question.', ''), 'title': title, 'text': text})
    # Elasticsearchではインデクシング時に正規化が行われる（馬鈴薯 > ジャガ芋）
    # 検索実行時の形態素解析結果を取得する
    url = 'http://localhost:' + str(server.local_bind_port) + '/' + index + '/_analyze'
    payload = {"analyzer": "sudachi_analyzer", 'text': query}
    headers = {'content-type': 'application/json'}
    r = requests.post(url, data=json.dumps(payload), headers=headers)
    tokens = []
    for t in r.json()['tokens']:
        tokens.append(t['token'])
    analyze = ' '.join(tokens)
    server.stop()
    return total, rows, analyze, elapsed_time


# 検索実行時の形態素解析結果を取得する
def get_analyze_ilu(query):
    server = connect('search-nlp01.answers-dev', 80)
    url = 'http://localhost:' + str(server.local_bind_port) + '/Kix/kix.ashx?t=' + query
    r = requests.get(url)
    tree = ElementTree.fromstring(r.content)
    analyze = tree.findtext('.//BasicQuery')
    server.stop()
    return analyze


# ABスクエアサーチのインデックスを検索する
def search_ilu(query):
    server = connect('search-index01.answers-dev', 12310)
    url = 'http://localhost:{0}/solr/1fc83ac7/nl?' \
          'facet.zeros=false&' \
          'facet=true&' \
          'defaultFields=ques_title;ques_text;best_answer&' \
          'ilu.synonym.expand=true&' \
          'facet.limit=-1&' \
          'ilu.aimai.kana=true&' \
          'json.nl=map&' \
          'wt=json&' \
          'hl=on&' \
          'rows=10&' \
          'fl=id,ques_title,ques_text,score,abx2_result_kex&' \
          'facet.sort=index&' \
          'start=0&' \
          'ilu.kix.useQueryType=Basic&' \
          'q="{1}"&' \
          'timeAllowed=8000&' \
          'facet.field=cid&' \
          'facet.field=need&' \
          'facet.field=ques_closed&' \
          'facet.field=qst_instructive_summary&' \
          'facet.field=answer_count&' \
          'qt=nl&' \
          'fq=&' \
          'fq=-cid:10000+OR+-cid:20000+OR+-cid:30000&'.format(str(server.local_bind_port), urllib.parse.quote(query))
    start = time.time()
    r = requests.get(url)
    elapsed_time = time.time() - start
    total = r.json()['response']['numFound']
    hits = r.json()['response']['docs']
    rows = []
    for row in hits:
        id = row['id']
        title = row['ques_title'][:60]
        text = row['ques_text'][:60]
        rows.append({'id': id, 'title': title, 'text': text})
    # 検索実行時の同義語情報を取得する
    synonyms = r.json()['ilu.synonymDiag']['expansion']
    synonym = ''
    for k, v in synonyms.items():
        synonym = synonym + '[' + ','.join(v) + ']'
    server.stop()
    return total, rows, synonym, elapsed_time


# 指定文字列で検索する
def search(query):
    es_total, es_rows, es_analyze, es_elapsed_time = search_es('okwave', query)
    es_flt_total, es_flt_rows, es_flt_analyze, es_flt_elapsed_time = search_es('okwave_flt', query)
    ilu_total, ilu_rows, ilu_synonym, ilu_elapsed_time = search_ilu(query)
    ilu_analyze = get_analyze_ilu(query)
    return es_total, es_analyze, es_rows, es_elapsed_time, \
           es_flt_total, es_flt_analyze, es_flt_rows, es_flt_elapsed_time, \
           ilu_total, ilu_analyze, ilu_synonym, ilu_rows, ilu_elapsed_time


# Elasticsearchのインデックスで類似文書検索を行う
def relatedqa_es(qid):
    server = connect('search01.answers-tmp', 9200)
    index = 'okwave_flt'
    url = 'http://localhost:' + str(server.local_bind_port) + '/' + index + '/_search'
    payload = {
        'profile': True,
        'size': 10,
        'query': {
            'bool': {
                'should': [
                    {
                        'more_like_this': {
                            'fields': ['contents_title'],
                            'like': [
                                {
                                    '_index': index,
                                    '_type': '_doc',
                                    '_id': 'question.' + qid
                                }
                            ],
                            'min_term_freq': 1,
                            'boost': 1.0
                        }
                    },
                    {
                        'more_like_this': {
                            'fields': ['contents_text'],
                            'like': [
                                {
                                    '_index': index,
                                    '_type': '_doc',
                                    '_id': 'question.' + qid
                                }
                            ],
                            'max_query_terms': 15,
                            'min_term_freq': 1,
                            'boost': 1.0
                        }
                    }
                ],
                'filter': {
                    'bool': {
                        'must': [
                            {'term': {'type': 'questions'}}
                        ]
                    }
                }
            }
        }
    }
    headers = {'content-type': 'application/json'}
    r = requests.post(url, data=json.dumps(payload), headers=headers)
    hits = r.json()['hits']['hits']
    rows_relatedqa = []
    for row in hits:
        id = row['_source']['id']
        title = row['_source']['contents_title'][:60]
        text = row['_source']['contents_text'][:60]
        rows_relatedqa.append({'id': id, 'title': title, 'text': text})
    server.stop()
    return rows_relatedqa


# ABスクエアサーチのインデックスで類似文書検索を行う
def relatedqa_ilu(qid):
    server = connect('search-index01.answers-dev', 12310)
    url = 'http://localhost:{0}/solr/relatedqa/select?' \
          'q="{1}"&' \
          'start=0&' \
          'rows=1&' \
          'wt=json&' \
          'json.nl=map&' \
          'ilu.prg=true&' \
          'ilu.prg.count=10&'.format(str(server.local_bind_port), qid)
    r = requests.get(url)
    hits = r.json()['response']['docs']
    rows = []
    rows_relatedqa = []
    # 最初の１件目が比較元のデータ（クエリで１件しか取得してない）
    for row in hits:
        id = row['id']
        title = row['ques_title'][:60]
        text = row['ques_text'][:60]
        rows.append({'id': id, 'title': title, 'text': text})
    # 質問データの付属情報に類似文書のデータがある
    if len(rows) > 0:
        hits_relatedqa = r.json()['ilu.prg'][qid]['docs']
        for row in hits_relatedqa:
            id = row['id']
            title = row['ques_title'][:60]
            text = row['ques_text'][:60]
            rows_relatedqa.append({'id': id, 'title': title, 'text': text})
    server.stop()
    return rows, rows_relatedqa


# 指定QIDで類似文書を検索する
def relatedqa(qid):
    ilu_rows, ilu_rows_relatedqa = relatedqa_ilu(qid)
    es_flt_rows_relatedqa = relatedqa_es(qid)
    return ilu_rows, ilu_rows_relatedqa, es_flt_rows_relatedqa


@app.route('/trial', methods=['POST', 'GET'])
def trial():
    query = ''
    if request.method == 'POST':
        query = request.form['query']
    es_total = 0
    es_analyze = ''
    es_rows = []
    es_elapsed_time = ''
    es_flt_total = 0
    es_flt_analyze = ''
    es_flt_rows = []
    es_flt_elapsed_time = ''
    ilu_total = 0
    ilu_analyze = ''
    ilu_synonym = ''
    ilu_rows = []
    ilu_elapsed_time = ''
    if len(query) > 0:
        es_total, es_analyze, es_rows, es_elapsed_time, \
        es_flt_total, es_flt_analyze, es_flt_rows, es_flt_elapsed_time, \
        ilu_total, ilu_analyze, ilu_synonym, ilu_rows, ilu_elapsed_time \
            = search(query)

    return render_template(
        'trial.html',
        query=query,
        es_total=es_total,
        es_analyze=es_analyze,
        es_rows=es_rows,
        es_elapsed_time=es_elapsed_time,
        es_flt_total=es_flt_total,
        es_flt_analyze=es_flt_analyze,
        es_flt_rows=es_flt_rows,
        es_flt_elapsed_time=es_flt_elapsed_time,
        ilu_total=ilu_total,
        ilu_analyze=ilu_analyze,
        ilu_synonym=ilu_synonym,
        ilu_rows=ilu_rows,
        ilu_elapsed_time=ilu_elapsed_time)


@app.route('/trial_relatedqa', methods=['POST', 'GET'])
def trial_relatedqa():
    qid = ''
    if request.method == 'POST':
        qid = request.form['qid']
    ilu_rows = []
    ilu_rows_relatedqa = []
    es_flt_rows_relatedqa = []
    no_data = False
    if len(qid) > 0:
        ilu_rows, ilu_rows_relatedqa, es_flt_rows_relatedqa = relatedqa(qid)
        # 指定したQIDの質問が存在しない場合の処理
        if len(ilu_rows) == 0:
            no_data = True

    return render_template(
        'trial_relatedqa.html',
        qid=qid,
        ilu_rows=ilu_rows,
        ilu_rows_relatedqa=ilu_rows_relatedqa,
        es_flt_rows_relatedqa=es_flt_rows_relatedqa,
        no_data=no_data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
