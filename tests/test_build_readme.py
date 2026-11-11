import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_readme as b  # noqa: E402

ROW = ('<li class="row" data-repo="%(slug)s" data-categories="%(cats)s" data-agents="claude-code">'
       '<span class="rank"><span>1</span></span><div class="repo-info"><img class="avatar" src="/a.webp" alt="">'
       '<a class="repo" href="/skill/%(slug)s/">%(repo)s</a><p class="desc">%(desc)s</p>'
       '<div class="row-tags"><a class="category" href="/category/x/">X</a></div></div> '
       '<div class="momentum"><div class="momentum-track"><svg viewBox="0 0 100 8"><rect width="7"/></svg></div>'
       '<span>%(growth)s</span></div> <div class="stars"><b>%(stars)s</b><span>GitHub stars</span>'
       '<button class="button stack-add" type="button">Add to stack</button></div> '
       '<div class="vote-panel"><a class="vote-box" href="#"><b class="vote-count">0</b></a></div></li>')


def row(slug='acme-skills', repo='acme/skills', desc='Skills for teams', growth='+2.6% over 7 days',
        stars='321.3k', cats='marketing writing'):
    return ROW % {'slug': slug, 'repo': repo, 'desc': desc, 'growth': growth, 'stars': stars, 'cats': cats}


def page(*rows):
    return '<html><body><p>5.6k repositories tracked</p><ol>' + ''.join(rows) + '</ol></body></html>'


class ParseRows(unittest.TestCase):
    def test_reads_the_fields_of_a_row(self):
        rows = b.parse_rows(page(row()))
        self.assertEqual(rows, [{'repo': 'acme/skills', 'slug': 'acme-skills', 'categories': ['marketing', 'writing'],
                                 'desc': 'Skills for teams', 'stars': '321.3k', 'growth': '+2.6%'}])

    def test_drops_rows_with_a_malformed_repo_slug_or_stars(self):
        rows = b.parse_rows(page(row(repo='not a repo'), row(slug='Bad Slug'), row(stars='lots'), row()))
        self.assertEqual([r['repo'] for r in rows], ['acme/skills'])

    def test_unknown_growth_stays_empty(self):
        self.assertEqual(b.parse_rows(page(row(growth='Insufficient history')))[0]['growth'], '')

    def test_reads_the_tracked_count(self):
        self.assertEqual(b.tracked_count(page(row())), '5.6k')


class CleanDescription(unittest.TestCase):
    def test_escapes_markdown_and_table_characters(self):
        self.assertEqual(b.clean_description('a|b [x](y) <img> *z*'), r'a\|b \[x\](y) \<img\> \*z\*')

    def test_removes_links_and_invisible_characters(self):
        text = 'See https://evil.example/x and www.spam.example now' + chr(0x202E) + chr(0x200B) + '!'
        self.assertEqual(b.clean_description(text), r'See and now\!')

    def test_cuts_long_text_at_a_word_with_an_ellipsis(self):
        out = b.clean_description('word ' * 60, limit=40)
        self.assertTrue(out.endswith(chr(0x2026)))
        self.assertLessEqual(len(out), 40)
        self.assertNotIn('wor' + chr(0x2026), out)

    def test_counts_wide_characters_twice(self):
        out = b.clean_description(chr(0x6F22) * 100, limit=20)
        self.assertEqual(out, chr(0x6F22) * 9 + chr(0x2026))


class Primary(unittest.TestCase):
    def test_keeps_only_repos_whose_first_category_matches(self):
        rows = b.parse_rows(page(row(slug='a-b', repo='a/b', cats='engineering legal'),
                                 row(slug='c-d', repo='c/d', cats='legal')))
        self.assertEqual([r['repo'] for r in b.primary(rows, 'legal')], ['c/d'])


class Build(unittest.TestCase):
    def fetcher(self, rows_per_page):
        def fetch(path):
            cats = path.split('/')[2] if path.startswith('/category/') else 'engineering'
            return page(*[row(slug='o%d-r%d' % (i, i), repo='o%d/r%d' % (i, i), cats=cats)
                          for i in range(rows_per_page)])
        return fetch

    def test_full_pages_pass_the_check_and_render(self):
        data = b.collect(self.fetcher(30), pause=0)
        self.assertEqual(b.check(data), [])
        text = b.render(data, datetime.date(2026, 11, 16))
        self.assertIn('**Last update: Nov 16, 2026**', text)
        self.assertIn('which checks 5.6k skill repositories every hour', text)
        self.assertIn('[o0/r0](https://hotskills.dev/skill/o0-r0/)', text)
        self.assertIn('### Legal', text)
        self.assertNotIn('Sponsored suggestion', text)

    def test_new_skips_repos_under_100_stars(self):
        def fetch(path):
            stars = ['99', '100', '1.2k'] if path == '/new/' else ['5k'] * 3
            return page(*[row(slug='o%d-r%d' % (i, i), repo='o%d/r%d' % (i, i), stars=s) for i, s in enumerate(stars)])
        self.assertEqual([r['stars'] for r in b.collect(fetch, pause=0)['new']], ['100', '1.2k'])

    def test_points_to_mcp_instead_of_pull_requests(self):
        text = b.render(b.collect(self.fetcher(30), pause=0), datetime.date(2026, 11, 16))
        self.assertIn('## No pull requests: use your agent', text)
        self.assertIn('(#no-pull-requests-use-your-agent)', text)
        self.assertIn('[set it up](https://hotskills.dev/mcp/)', text)

    def test_thin_pages_fail_the_check(self):
        data = b.collect(self.fetcher(2), pause=0)
        self.assertTrue(b.check(data))

    def test_main_writes_nothing_when_the_check_fails(self):
        original = b.collect
        thin = original(self.fetcher(2), pause=0)
        b.collect = lambda: thin
        out = os.path.join(os.path.dirname(__file__), '_should_not_exist.md')
        try:
            self.assertEqual(b.main(['--out', out]), 1)
            self.assertFalse(os.path.exists(out))
        finally:
            b.collect = original


if __name__ == '__main__':
    unittest.main()
