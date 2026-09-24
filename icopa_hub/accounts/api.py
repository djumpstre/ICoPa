"""
APIs for user authentication
"""

import logging

from django.core.exceptions import PermissionDenied
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .models import UserToken
from .serializers import LoginSerializer, RegisterSerializer, UserSerializer, UserTokenRefreshSerializer


logger = logging.getLogger("icopa.auth.api")



class UserRegisterAPIView(generics.GenericAPIView):
    """
    API for registering a new user
    - POST Request
    Example:
    {   'username': 'dummy', 
        'password': 'dummy',
        'email': 'dummy@xxx'}
    """
    serializer_class = RegisterSerializer

    def post(self, request, *args, **kwargs):
        """
        Get the user data from request and create a new user
        """
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid(raise_exception=True):
            result = serializer.save()
            return Response({
                "user": UserSerializer(result["user"], context=self.get_serializer_context()).data,
                "access": result["access"],
                "refresh": result["refresh"],
            }, 
                status=status.HTTP_201_CREATED)
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)


class UserLoginAPIView(generics.GenericAPIView):
    """
    API for logging in an existing user with password.
    Handle POST request.
    
    Request data should be in this format: 
    {
        'username': 'dummy',
        'password': 'dummy'
    }
    
    Upon successful login, the response will be in this format:
    {
        'user': user details 
        'token': token
    }
    
    If login is unsuccessful, a 400 Bad Request error will be returned with error details.
    """
    serializer_class = LoginSerializer

    def post(self, request, *args, **kwargs):
        """
        User login with password
        """
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid(raise_exception=True):
            user = serializer.validated_data["user"]
            
            logger.info("Validated user data: %s", user)

            refresh = RefreshToken.for_user(user)
            UserToken.record_refresh(user, refresh)
            return Response({
                "user": UserSerializer(user, context=self.get_serializer_context()).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                }, status=status.HTTP_200_OK)

        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)


class UserAPIView(generics.RetrieveAPIView):
    """
    API for getting user data through JWT token.
    
    GET Request: 
        - Header: Authorization: Bearer <token>
    
    Returns: 
        - User data in JSON format
        
    If the user is not authenticated, a 403 Forbidden error will be returned.
    """

    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]
    serializer_class = UserSerializer

    def get_object(self):
        """
        Get user through JWT token.
        """
        user = self.request.user
        if user.is_authenticated:
            logger.debug("Authenticated user from request: %s", user)
            return user
        else:
            logger.warning("User is not authenticated.")
            raise PermissionDenied({
                "message": "Invalid user."
                })


class UserLogoutAPIView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        refresh_token_str = request.data.get("refresh")
        if refresh_token_str:
            try:
                refresh = RefreshToken(refresh_token_str)
                UserToken.objects.filter(
                    user=request.user,
                    jti=str(refresh["jti"]),
                    revoked=False,
                ).update(revoked=True)
            except Exception as exc:
                raise ValidationError({"refresh": "Invalid refresh token."}) from exc
        else:
            UserToken.objects.filter(user=request.user, revoked=False).update(revoked=True)

        return Response(status=status.HTTP_204_NO_CONTENT)


class UserTokenRefreshAPIView(TokenRefreshView):
    serializer_class = UserTokenRefreshSerializer


class UserDeleteAPIView(generics.DestroyAPIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user
