import pytest
from employer_feeds import configured_boards, fetch_board, relevant, FeedError

def test_config_validation(monkeypatch):
    monkeypatch.setenv('EMPLOYER_BOARDS_JSON','[{"name":"Sample","provider":"greenhouse","board":"sample"}]')
    assert configured_boards()[0]['board']=='sample'
    monkeypatch.setenv('EMPLOYER_BOARDS_JSON','[{"name":"Bad","provider":"greenhouse","board":"../../etc"}]')
    with pytest.raises(FeedError): configured_boards()

class Response:
    def raise_for_status(self): pass
    def json(self):
        return {'jobs':[{'id':123,'title':'Paralegal','absolute_url':'https://boards.greenhouse.io/sample/jobs/123','location':{'name':'Nottingham'},'content':'<p>Case management</p>'}]}
class Session:
    def get(self,url,**kwargs):
        assert url.startswith('https://boards-api.greenhouse.io/')
        return Response()

def test_greenhouse_parsing():
    jobs=fetch_board({'name':'Sample','provider':'greenhouse','board':'sample'},Session())
    assert len(jobs)==1
    assert jobs[0]['salary_verified']==0
    assert relevant(jobs[0],'paralegal,compliance','Nottingham')
    assert not relevant(jobs[0],'engineer','')

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH",str(tmp_path/"test.db"))
    monkeypatch.delenv("APP_USERNAME",raising=False)
    monkeypatch.delenv("APP_PASSWORD",raising=False)
    from database import init_db
    init_db()
    from app import app
    app.config.update(TESTING=True)
    return app.test_client()

def test_feed_import(client,monkeypatch):
    import app
    monkeypatch.setattr(app,'configured_boards',lambda:[{'name':'Sample','provider':'greenhouse','board':'sample'}])
    monkeypatch.setattr(app,'fetch_board',lambda board:fetch_board(board,Session()))
    for _ in range(2):
        assert client.post('/employer-feeds/import',data={'keywords':'paralegal','location':'Nottingham'}).status_code==302
    from database import connect
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE source_id='greenhouse:sample:123'").fetchone()[0]==1
