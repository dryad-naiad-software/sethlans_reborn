# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
URLconf of setup mode (FR-SM-3). Every other path answers a JSON 404;
the front end's built files are served by WhiteNoise in front of Django
(``serve``).
"""
from django.urls import path

from sethlans_manager.setup_mode import views

urlpatterns = [
    path("api/health/", views.health, name="setup-health"),
    path("api/setup/auth/", views.auth_view, name="setup-auth"),
    path("api/setup/form/", views.form_view, name="setup-form"),
    path("api/setup/submit/", views.submit_view, name="setup-submit"),
    path("setup/", views.setup_page, name="setup-page"),
]

handler404 = "sethlans_manager.setup_mode.views.not_found"
handler500 = "sethlans_manager.setup_mode.views.server_error"
