VERSION = (0, 2, 3)

from django.conf import settings
from django.contrib.auth import get_user, SESSION_KEY
from django.core.cache import caches
from django.db.models.signals import post_save, post_delete
from django.core.exceptions import ObjectDoesNotExist
from django.utils.functional import SimpleLazyObject

from django.contrib.auth.models import AnonymousUser

try:
    from django.contrib.auth import get_user_model
except ImportError:
    from django.contrib.auth.models import User
    get_user_model = lambda: User

CACHE_KEY = 'cached_auth_middleware:%s'


try:
    from django.apps import apps
    get_model = apps.get_model
except ImportError:
    from django.db.models import get_model


user_profile_accessor = "profile"
if hasattr(settings, 'CACHED_AUTH_USER_PROFILE_ACCESSOR_FIELD'):
    user_profile_accessor = settings.CACHED_AUTH_USER_PROFILE_ACCESSOR_FIELD


def profile_preprocessor(user, request):
    """ Cache user profile """
    try:
        getattr(user, user_profile_accessor).pk
    # Handle exception for user with no profile and AnonymousUser
    except (ObjectDoesNotExist, AttributeError):
        pass
    return user


user_preprocessor = None
if hasattr(settings, 'CACHED_AUTH_PREPROCESSOR'):
    tmp = settings.CACHED_AUTH_PREPROCESSOR.split(".")
    module_name, function_name = ".".join(tmp[0:-1]), tmp[-1]
    func = getattr(__import__(module_name, fromlist=['']), function_name)
    if callable(func):
        user_preprocessor = func
    else:
        raise Exception("CACHED_AUTH_PREPROCESSOR must be callable with 2 arguments user and request")
else:
    user_preprocessor = profile_preprocessor


cache_backend = "default"
if hasattr(settings, 'CACHED_AUTH_CACHE_BACKEND'):
   cache_backend = settings.CACHED_AUTH_CACHE_BACKEND

cache = caches[cache_backend]


def invalidate_cache(sender, instance, **kwargs):
    if isinstance(instance, get_user_model()):
        key = CACHE_KEY % instance.id
    else:
        key = CACHE_KEY % instance.user_id
    cache.delete(key)


def get_cached_user(request):
    if not hasattr(request, '_cached_user'):
        try:
            key = CACHE_KEY % request.session[SESSION_KEY]
            user = cache.get(key)
        except KeyError:
            user = AnonymousUser()

        if user is None:
            user = get_user(request)
            if user_preprocessor:
                user = user_preprocessor(user, request)
            cache.set(key, user)
        request._cached_user = user
    return request._cached_user


class Middleware(object):

    def __init__(self):
        user_model = get_user_model()
        post_save.connect(invalidate_cache, sender=user_model)
        post_delete.connect(invalidate_cache, sender=user_model)
        accessor_field = getattr(user_model, user_profile_accessor, None)
        if accessor_field:
            profile_model = accessor_field.related.related_model

            if profile_model:
                post_save.connect(invalidate_cache, sender=profile_model)
                post_delete.connect(invalidate_cache, sender=profile_model)

    def process_request(self, request):
        assert hasattr(request, 'session'), "The Django authentication middleware requires session middleware to be installed. Edit your MIDDLEWARE_CLASSES setting to insert 'django.contrib.sessions.middleware.SessionMiddleware'."
        request.user = SimpleLazyObject(lambda: get_cached_user(request))
