import csv
import io
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import catalog_engine
import catalog_types_v8_11 as catalog_types
import web


class WebReportTest(unittest.TestCase):
    def test_confirmed_pair_accepts_both_piton_names(self):
        for names in (
            ('PitON E3',) * 4,
            ('PitON Electric E3',) * 4,
            ('PitON E3', 'PitON Electric E3') * 2,
        ):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                db = root / 'catalogs.sqlite3'
                shutil.copyfile(catalog_engine.__file__, root / 'catalog_engine.py')
                with patch.object(catalog_engine, 'DB', db):
                    catalog_engine.init()

                with sqlite3.connect(db) as conn:
                    conn.executemany(
                        "UPDATE specs SET product=? WHERE vendor='piton' AND parameter=?",
                        zip(names, catalog_engine.META),
                    )
                    conn.execute("INSERT INTO documents VALUES ('g', 'Graziadio ISOLSBARRA', 'g.pdf', 1, 1, '', '')")
                    catalog_types.record_classification(conn, 'g', 'graziadio', 'Graziadio ISOLSBARRA', 'shp')

                with (
                    patch.multiple(web, ROOT=root, DB=db, REPORTS=root / 'reports'),
                    web.ThreadingHTTPServer(('127.0.0.1', 0), web.Handler) as server,
                ):
                    thread = threading.Thread(target=server.serve_forever)
                    thread.start()
                    try:
                        base = f'http://127.0.0.1:{server.server_port}'
                        opener = build_opener(ProxyHandler({}))

                        def request(path, data=None):
                            with opener.open(Request(base + path, data=data), timeout=5) as response:
                                return response.headers.get_content_type(), response.read()

                        matrix = json.loads(request('/api/matrix-v7.json')[1])
                        self.assertEqual(matrix['totals']['missing'], 0)
                        report = json.loads(request('/api/report', b'{"material":"Cu","rating":4000}')[1])
                        self.assertTrue(report['ok'])

                        content_type, html = request(report['links']['html'])
                        self.assertEqual(content_type, 'text/html')
                        self.assertIn('<td>120 kA', html.decode())
                        self.assertIn('<td>156 kA', html.decode())

                        content_type, csv_bytes = request(report['links']['csv'])
                        self.assertEqual(content_type, 'text/csv')
                        rows = list(csv.reader(io.StringIO(csv_bytes.decode('utf-8-sig')), delimiter=';'))
                        self.assertEqual(len(rows), 5)
                        self.assertEqual((rows[2][1], rows[2][3]), ('120 kA', '156 kA'))

                        content_type, json_bytes = request(report['links']['json'])
                        self.assertEqual(content_type, 'application/json')
                        items = {row['parameter']: row for row in json.loads(json_bytes)['items']}
                        self.assertEqual(len(items), 4)
                        self.assertEqual((items['icw_1s_ka']['piton'], items['icw_1s_ka']['graziadio']), (120, 156))

                        with sqlite3.connect(db) as conn:
                            conn.execute("UPDATE specs SET product='Unrelated' WHERE vendor='piton' AND parameter='icw_1s_ka'")
                        with self.assertRaises(HTTPError) as error:
                            request('/api/report', b'{"material":"Cu","rating":4000}')
                        self.assertEqual(error.exception.code, 409)
                    finally:
                        server.shutdown()
                        thread.join()


if __name__ == '__main__':
    unittest.main()
