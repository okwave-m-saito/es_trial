# es_trial
Elasticsearch の動作検証を行うためのサンプルアプリ

## 環境構築とアプリケーションの実行

uwsgi.ini の base を環境に合わせて編集する
```
base = /home/m_saito/PycharmProjects/es_trial
```

dotenv.sample をコピーして環境に合わせて編集する
```
$ cp dotenv.sample .env
```

/etc/nginx/conf.d/es_trial.conf のように個別の設定を作成する
```
server {
  listen 8080;
  error_log /home/develop/workspace/es_trial/error.txt warn;

  location / {
    include uwsgi_params;
    #uwsgi_pass unix:///home/m_saito/workspace/es_trial/uwsgi.sock;
    uwsgi_pass unix:///tmp/uwsgi.sock;
  }
}
```

uwsgi をバックグラウンドで起動する
```
$ virtualenv env
$ . env/bin/activate
$ pip install -r requirements.txt
$ env/bin/uwsgi --ini uwsgi.ini &
```

止める場合は uwsgi.sock を削除する
```
$ rm /tmp/uwsgi.sock
```

