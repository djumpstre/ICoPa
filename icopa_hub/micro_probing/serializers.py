from rest_framework import serializers

from inventory.models import VM
from .models import ProbeSession, ProbeWindow


class ProbeSessionSerializer(serializers.ModelSerializer):
    source_vm = serializers.PrimaryKeyRelatedField(queryset=VM.objects.none())
    target_vm = serializers.PrimaryKeyRelatedField(queryset=VM.objects.none())
    duration_sec = serializers.IntegerField(min_value=1, max_value=10, default=5)
    interval_sec = serializers.IntegerField(min_value=1, max_value=30, default=10)
    window_count = serializers.IntegerField(min_value=1, max_value=60, default=6)

    class Meta:
        model = ProbeSession
        fields = ["id", "source_vm", "target_vm", "duration_sec", "interval_sec", "window_count", "status", "error_message", "created_at", "started_at", "finished_at"]
        read_only_fields = ["id", "status", "error_message", "created_at", "started_at", "finished_at"]
        validators = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request:
            for name in ("source_vm", "target_vm"):
                self.fields[name].queryset = VM.objects.filter(created_by=request.user, archived=False)

    def validate(self, attrs):
        if attrs["source_vm"] == attrs["target_vm"]:
            raise serializers.ValidationError("Source and target must be different inventory VMs.")
        credential = attrs["source_vm"].credential
        if credential and credential.created_by_id != self.context["request"].user.pk:
            raise serializers.ValidationError("Source SSH credential belongs to another user.")
        return attrs


class ProbeWindowSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProbeWindow
        fields = ["id", "sequence", "measured_at", "source_address", "target_address", "metrics", "error_message"]


class LookupQuerySerializer(serializers.Serializer):
    max_age_sec = serializers.IntegerField(min_value=1, max_value=86400, default=60)
    source_vm = serializers.IntegerField(min_value=1, required=False)
    target_vm = serializers.IntegerField(min_value=1, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=500, default=100)
