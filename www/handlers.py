#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'Michael Liao'

' url handlers '

import re, time, json, logging, hashlib, base64, asyncio

import markdown2

from aiohttp import web

from coroweb import get, post
from apis import Page, APIValueError,APIPermissionError,APIResourceNotFoundError,APIError

from models import User, Comment, Blog, Follow,Appreciate,Conversation,next_id
from config import configs

COOKIE_NAME = 'awesession'
_COOKIE_KEY = configs.session.secret

def no_cache(resp):
    headers={'Cache-Control':'no-cache','Pragma':'no-cache'}
    resp.headers=headers
    resp.Expires=0
    return resp
    
def check_admin(request):
    if request.__user__ is None or not request.__user__.admin:
        raise APIPermissionError('no Permission')

def check_passwd(email,passwd):
    if not email:
        raise APIValueError('email', 'Invalid email.')
    if not passwd:
        raise APIValueError('passwd', 'please input password.')
    users = yield from User.findAll('email=?', [email])
    if len(users) == 0:
        raise APIValueError('email', 'Email not exist.')
    user = users[0]
    # check passwd:
    sha1 = hashlib.sha1()
    sha1.update(user.id.encode('utf-8'))
    sha1.update(b':')
    sha1.update(passwd.encode('utf-8'))
    if user.passwd != sha1.hexdigest():
        raise APIValueError('passwd', 'password is fault.')
    return user

def get_page_index(page_str):
    p = 1
    try:
        p = int(page_str)
    except ValueError as e:
        pass
    if p < 1:
        p = 1
    return p

def user2cookie(user, max_age):
    '''
    Generate cookie str by user.
    '''
    # build cookie string by: id-expires-sha1
    expires = str(int(time.time() + max_age))
    s = '%s-%s-%s-%s' % (user.id, user.passwd, expires, _COOKIE_KEY)
    L = [user.id, expires, hashlib.sha1(s.encode('utf-8')).hexdigest()]
    return '-'.join(L)

def text2html(text):
    lines = map(lambda s: '<p>%s</p>' % s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), filter(lambda s: s.strip() != '', text.split('\n')))
    return ''.join(lines)

@asyncio.coroutine
def cookie2user(cookie_str):
    '''
    Parse cookie and load user if cookie is valid.
    '''
    if not cookie_str:
        return None
    try:
        L = cookie_str.split('-')
        if len(L) != 3:
            return None
        uid, expires, sha1 = L
        if int(expires) < time.time():
            return None
        user = yield from User.find(uid)
        if user is None:
            return None
        s = '%s-%s-%s-%s' % (uid, user.passwd, expires, _COOKIE_KEY)
        if sha1 != hashlib.sha1(s.encode('utf-8')).hexdigest():
            logging.info('invalid sha1')
            return None
        user.passwd = '******'
        return user
    except Exception as e:
        logging.exception(e)
        return None

@get('/')
def index(*, page='1'):
    page_index = get_page_index(page)
    num = yield from Blog.findNumber('count(id)')
    page = Page(num)
    if num == 0:
        blogs = []
    else:
        blogs = yield from Blog.findAll(orderBy='created_at desc', limit=(page.offset, page.limit))
    return {
        '__template__': 'blogs.html',
        'page': page,
        'blogs': blogs
    }
@get('/user/{name}')
def user(name,*,page='1'):
    user = yield from User.find(name,'name')
    if user is None:
        raise APIValueError('404')
    page_index = get_page_index(page)
    num = yield from Blog.findNumber('count(id)','user_name=?',name)
    page = Page(num)
    if num == 0:
        blogs = []
    else:
        blogs = yield from Blog.findAll('user_name=?',[name],orderBy='created_at desc', limit=(page.offset, page.limit))
    
    return {
        '__template__': 'user.html',
        'page': page,
        'blogs': blogs,
        'user':user
    }


@get('/blog/{id}')
def get_blog(id):
    blog = yield from Blog.find(id)
    comments = yield from Comment.findAll('blog_id=?', [id], orderBy='created_at desc')
    for c in comments:
        c.html_content = text2html(c.content)
    blog.html_content = markdown2.markdown(blog.content)
    return {
        '__template__': 'blog.html',
        'blog': blog,
        'comments': comments
    }

@get('/register')
def register():
    return {
        '__template__': 'register.html'
    }

@get('/signin')
def signin():
    return {
        '__template__': 'signin.html'
    }

@get('/setting')
def change():
    return {
        '__template__': 'setting.html'
    }

@post('/api/follow')
def follow(request,*,ownerid,state):
    fromid=request.__user__.id
    if fromid is None:
        raise APIPermissionError("请登录后关注")
    num = yield from Follow.findNumber('count(id)','from_user_id=\''+fromid+'\' and to_user_id=?',ownerid)
    if state == 'check':
        if num is None:
            num=0
        else:
            num=1
        return dict(message=num)
    
    if num:
        follow=yield from Follow.find(fromid,'from_user_id',ownerid,'to_user_id')
        yield from follow.remove()
        return dict(message=0)
    else:
        follow=Follow(from_user_id=fromid,to_user_id=ownerid)
        yield from follow.save()
        return dict(message=1)

@post('/api/authenticate')
def authenticate(*, email, passwd):
    user = yield from check_passwd(email,passwd)
    # authenticate ok, set cookie:
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.passwd = '******'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return r

@post('/api/setting/password')
def change_password(*, email, passwd,newPassword):
    user = yield from check_passwd(email,passwd)
    sha1 = hashlib.sha1()
    sha1.update(user.id.encode('utf-8'))
    sha1.update(b':')
    sha1.update(newPassword.encode('utf-8'))
    user.passwd=sha1.hexdigest()
    yield from user.update()
    return dict(message='sussess')

@get('/signout')
def signout(request):
    referer = request.headers.get('Referer')
    r = web.HTTPFound(referer or '/')
    r.set_cookie(COOKIE_NAME, '-deleted-', max_age=0, httponly=True)
    logging.info('user signed out.')
    return r

@get('/manage')
def manage():
    return 'redirect:/manage/comments'

@get('/manage/{items}')
def manage_comments(items,*, page='1'):
    if items!='blogs' and items!='users' and items!='comments':
        raise APIValueError('404')
    return {
        '__template__': 'manage_items.html',
        'page_index': get_page_index(page)
    }

@get('/manage/blogs/create')
def manage_create_blog(request):
    return {
        '__template__': 'manage_blog_edit.html',
        'id': '',
        'action': '/api/blogs'
    }

@get('/manage/blogs/edit')
def manage_edit_blog(*, id):
    return {
        '__template__': 'manage_blog_edit.html',
        'id': id,
        'action': '/api/blogs/%s' % id
    }

@post('/api/blogs/{id}/comments')
def api_create_comment(id, request, *, content):
    user = request.__user__
    if user is None:
        raise APIPermissionError('Please signin first.')
    if not content or not content.strip():
        raise APIValueError('content')
    blog = yield from Blog.find(id)
    if blog is None:
        raise APIResourceNotFoundError('Blog')
    comment = Comment(blog_id=blog.id, user_id=user.id, user_name=user.name, user_image=user.image, content=content.strip())
    yield from comment.save()
    return comment

@post('/api/comments/{id}/delete')
def api_delete_comments(id, request):
    c = yield from Comment.find(id)
    if c is None:
        raise APIResourceNotFoundError('Comment')
    if request.__user__.admin or c.user_id==request.__user__.id:
        yield from c.remove()
        return dict(message='delete id finished')

@post('/api/users/{id}/delete')
def api_delete_users(id, request):
    c = yield from User.find(id)
    if c is None:
        raise APIResourceNotFoundError('Comment')
    if request.__user__.admin:
        yield from c.remove()
        return dict(message='delete id finished')


@get('/api/{tablename}')
def api_items(tablename,*, page='1'):
    logging.info('testtesttesttest')
    selects={'users':User,'comments':Comment,'blogs':Blog}
    item=selects.get(tablename,None)
    if item is None:
        raise APIValueError('404')
    page_index = get_page_index(page)
    num = yield from item.findNumber('count(id)')
    p = Page(num, page_index)
    if num == 0:
        return dict(page=p, items=())
    items = yield from item.findAll(orderBy='created_at desc', limit=(p.offset, p.limit))
    
    for item in items:
        item.password="******"
        if item==Comment:
            if len(item.content)>20:
                item.content=item.content[:20]+'   ···'
    return dict(page=p, items=items)

_RE_EMAIL = re.compile(r'^[a-z0-9\.\-\_]+\@[a-z0-9\-\_]+(\.[a-z0-9\-\_]+){1,4}$')
_RE_SHA1 = re.compile(r'^[0-9a-f]{40}$')

@post('/api/register')
def api_register_user(*, email, name, passwd):
    if not name or not name.strip():
        raise APIValueError('name')
    if not email or not _RE_EMAIL.match(email):
        raise APIValueError('email')
    if not passwd or not _RE_SHA1.match(passwd):
        raise APIValueError('passwd')
    users = yield from User.findAll('email=?', [email])
    users2 = yield from User.findAll('name=?', [name])
    if len(users) > 0:
        raise APIError('register:failed', 'email', 'Email is already in use.')
    if len(users2) > 0:
        raise APIError('register:failed', 'name', 'name is already in use.')
    uid = next_id()
    sha1_passwd = '%s:%s' % (uid, passwd)
    user = User(id=uid, name=name.strip(), email=email, passwd=hashlib.sha1(sha1_passwd.encode('utf-8')).hexdigest(), image='http://www.gravatar.com/avatar/%s?d=mm&s=120' % hashlib.md5(email.encode('utf-8')).hexdigest())
    yield from user.save()
    # make session cookie:
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.passwd = '******'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return r

@get('/api/blogs/{id}')
def api_get_blog(*, id):
    blog = yield from Blog.find(id)
    return blog

@post('/api/blogs')
def api_create_blog(request, *, name, summary, content):
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    if request.__user__ is None:
        raise APIPermissionError('please login in')
    blog = Blog(user_id=request.__user__.id, user_name=request.__user__.name, user_image=request.__user__.image, name=name.strip(), summary=summary.strip(), content=content.strip())
    yield from blog.save()
    return blog

@post('/api/blogs/{id}')
def api_update_blog(id, request, *, name, summary, content):
    blog = yield from Blog.find(id)
    if request.__user__.admin or request.__user__.id == blog.user_id :
        if not name or not name.strip():
            raise APIValueError('name', 'name cannot be empty.')
        if not summary or not summary.strip():
            raise APIValueError('summary', 'summary cannot be empty.')
        if not content or not content.strip():
            raise APIValueError('content', 'content cannot be empty.')
        blog.name = name.strip()
        blog.summary = summary.strip()
        blog.content = content.strip()
        blog.update_at = time.time()
        yield from blog.update()
        return blog
    else:
        raise APIPermissionError('you have not permission')

@post('/api/blogs/{id}/delete')
def api_delete_blog(request, *, id):
    blog = yield from Blog.find(id)
    if blog is None:
        raise APIResourceNotFoundError('blog')
    if not request.__user__.admin and blog.user_id!=request.__user__.id:
        raise APIPermissionError('no Permission')
    yield from blog.remove()
    comments = yield from Comment.findAll('blog_id=?', [id], orderBy='created_at desc')
    if len(comments) != 0:
        for comment in comments:
            yield from comment.remove()
    return dict(id=id)
