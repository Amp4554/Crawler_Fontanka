import json
import re
import sqlite3
import time
from datetime import datetime
from html import unescape
from urllib.parse import urljoin
import requests


class CrawlerLenta:
    BASE_URL = 'https://lenta.ru/search/v2/process'

    HEADERS = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'cache-control': 'max-age=0',
        'priority': 'u=0, i',
        'sec-ch-ua': '"Not-A.Brand";v="99", "Google Chrome";v="157", "Chromium";v="157"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'none',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36',
    }

    PARAMS = {
        'query': 'пулково',
        'from': '0',
        'size': '100',
        'sort': '2',
        'title_only': '0',
        'domain': '1',
        'modified,format': 'yyyy-MM-dd',
    }

    def __init__(self):
        self.keywords = []
        self.data = []
        self.total_article = 0

    def extract(self, data: dict):
        try:
            for elem in data["matches"]:
                self.data.append({"url": elem["url"],
                                  "title": elem["title"],
                                  "text": elem["text"],
                                  "pubdate": elem["pubdate"]})
        except KeyError:
            print(data)

    def make_request(self, keyword: str):
        params = self.PARAMS.copy()
        params["query"] = keyword
        response = requests.get(url=self.BASE_URL, headers=self.HEADERS, params=params).json()
        self.total_article = response["total"]
        self.extract(response)
        while len(self.data) < int(self.total_article):
            params = self.PARAMS.copy()
            params["query"] = keyword
            params["from"] = str(len(self.data))
            response = requests.get(url=self.BASE_URL, headers=self.HEADERS, params=params).json()
            self.extract(response)
        self.insert_data(self.data)
        self.data = []

    def get_database_keywords(self):
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        query = "SELECT keyword FROM keywords"
        cursor.execute(query)
        keywords = cursor.fetchall()
        for keyword in keywords:
            self.keywords.append(keyword[0])
        cursor.close()
        conn.close()

    def insert_data(self, data: list):
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        for elem in data:
            query = "INSERT INTO articles(url, title, text, pubdate) VALUES(?,?,?,?)"
            cursor.execute(query, (elem["url"], elem["title"], elem["text"], elem["pubdate"]))
            conn.commit()
            print(f"INSERT URL {elem['url']} SUCCESS")
        cursor.close()
        conn.close()


class CrawlerFontanka(CrawlerLenta):
    BASE_URL = 'https://www.fontanka.ru/search/'

    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def get_page(self, url, params=None):
        time.sleep(1)
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.content.decode('utf-8')

    def search_urls(self, keyword):
        urls = []
        url = self.BASE_URL
        params = {'keywords': keyword}
        visited = set()
        while url and url not in visited:
            visited.add(url)
            html = self.get_page(url, params)
            match = re.search(r'window\.initialState\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
            state = json.loads(match.group(1))
            page = state['searchPage']
            for card in page['announcements']:
                href = (card.get('header') or {}).get('url')
                if href:
                    urls.append(urljoin(url, href).split('?')[0])
            pagination = page['meta']['pagination']
            if not pagination['hasNextPage'] or not pagination['nextPageUrl']:
                break
            url = urljoin(url, pagination['nextPageUrl'])
            params = None
        return urls

    def read_article(self, url):
        html = self.get_page(url)
        blocks = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        for block in blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                nodes = data.get('@graph', [data])
            else:
                nodes = data
            for node in nodes:
                if isinstance(node, dict) and node.get('articleBody') and node.get('datePublished'):
                    pubdate = datetime.fromisoformat(node['datePublished']).timestamp()
                    return {"url": url,
                            "title": node["headline"],
                            "text": unescape(node["articleBody"]),
                            "pubdate": int(pubdate)}
        raise ValueError("Нет текста статьи")

    def make_request(self, keyword: str):
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM articles WHERE url LIKE '%fontanka.ru/%'")
        known_urls = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()

        try:
            urls = self.search_urls(keyword)
        except Exception as error:
            print(f"Поиск по {keyword} не удался: {error}")
            return

        saved = 0
        for url in urls:
            if url in known_urls:
                continue
            try:
                article = self.read_article(url)
            except Exception as error:
                print(f"Пропущена статья {url}: {error}")
                continue
            self.insert_data([article])
            known_urls.append(url)
            saved += 1
        print(f"Фонтанка «{keyword}»: найдено в поиске {len(urls)}, сохранено новых {saved}")


def main():
    lenta = CrawlerLenta()
    fontanka = CrawlerFontanka()

    lenta.get_database_keywords()
    if not lenta.keywords:
        print("Таблица пуста")
        return

    for keyword in lenta.keywords:
        lenta.make_request(keyword=keyword)
        fontanka.make_request(keyword=keyword)


if __name__ == '__main__':
    main()