# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
URL configuration for sethlans_manager project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# sethlans_manager/urls.py

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.generic import TemplateView
from django.views.static import serve as static_serve

# --- NEW: Import DRF Spectacular views ---
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path('admin/', admin.site.urls),
    # --- API documentation URLs ---
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    # Setup wizard API — must precede the generic api/ include so
    # /api/setup/ routes are matched before the DRF router catch-all.
    path('api/setup/', include('workers.urls_setup')),
    path('api/', include('workers.urls')),

    # Always-on media file serving. This is appropriate because the app is a
    # locally-installed render farm manager, not a public internet deployment.
    re_path(
        r'^media/(?P<path>.*)$',
        static_serve,
        {'document_root': settings.MEDIA_ROOT},
    ),

    # SPA catch-all — serves the Angular index.html for any route not matched
    # above. Ordering is load-bearing: API, admin, media, and static prefixes
    # are excluded so Django handles them normally.
    re_path(
        r'^(?!api/|admin/|media/|static/).*$',
        TemplateView.as_view(template_name='index.html'),
    ),
]
