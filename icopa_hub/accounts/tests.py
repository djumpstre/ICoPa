"""
Test cases for authentication API endpoints.
"""

import uuid

from rest_framework import status
from rest_framework.test import APITestCase


class AuthAPITests(APITestCase):
    """
        Test the authentication API endpoints
    """

    def setUp(self):
        self.register_url = "/auth/user_register/"
        self.login_url = "/auth/user_login/"
        self.user_url = "/auth/user/"
        self.logout_url = "/auth/user_logout/"
        self.refresh_url = "/auth/token_refresh/"
        self.delete_url = "/auth/user_delete/"
        self.username = f"test_user_{uuid.uuid4().hex[:8]}"
        self.password = "testpass123"

    def test_register_login_user_logout_flow(self):
        register_response = self.client.post(
            self.register_url,
            {
                "username": self.username,
                "password": self.password,
                "email": "test_user@example.com",
            },
            format="json",
        )

        self.assertEqual(register_response.status_code,
                         status.HTTP_201_CREATED)
        self.assertIn("access", register_response.data)
        self.assertIn("refresh", register_response.data)
        print(f"[auth] <create user> success: {self.username}")

        login_response = self.client.post(
            self.login_url,
            {"username": self.username, "password": self.password},
            format="json",
        )

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertIn("access", login_response.data)
        self.assertIn("refresh", login_response.data)
        print(f"[auth] <login> success: {self.username}")

        access_token = login_response.data["access"]
        refresh_token = login_response.data["refresh"]

        user_response = self.client.get(
            self.user_url,
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        self.assertEqual(user_response.status_code, status.HTTP_200_OK)
        self.assertEqual(user_response.data["username"], self.username)
        print(f"[auth] <get user> success: {self.username}")

        logout_response = self.client.post(
            self.logout_url,
            {"refresh": refresh_token},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        self.assertIn(
            logout_response.status_code,
            [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT],
        )
        print(f"[auth] <logout> success: {self.username}")

        refresh_response = self.client.post(
            self.refresh_url,
            {"refresh": refresh_token},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", refresh_response.data)

        delete_response = self.client.delete(
            self.delete_url,
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        self.assertIn(
            delete_response.status_code,
            [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT],
        )
        print(f"[auth] <delete user> success: {self.username}")

    def register(self):
        response = self.client.post(self.register_url, {
            "username": self.username, "password": self.password,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response.data

    def test_registration_refresh_is_recorded_and_can_be_revoked(self):
        tokens = self.register()
        response = self.client.post(self.refresh_url, {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.post(self.logout_url, {"refresh": tokens["refresh"]},
                                    format="json", HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        response = self.client.post(self.refresh_url, {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_without_refresh_revokes_all_sessions(self):
        registered = self.register()
        logged_in = self.client.post(self.login_url, {
            "username": self.username, "password": self.password,
        }, format="json").data
        response = self.client.post(self.logout_url, {}, format="json",
                                    HTTP_AUTHORIZATION=f"Bearer {logged_in['access']}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        for tokens in (registered, logged_in):
            response = self.client.post(self.refresh_url, {"refresh": tokens["refresh"]}, format="json")
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_untracked_refresh_is_rejected(self):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.tokens import RefreshToken

        self.register()
        user = get_user_model().objects.get(username=self.username)
        refresh = RefreshToken.for_user(user)
        response = self.client.post(self.refresh_url, {"refresh": str(refresh)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
