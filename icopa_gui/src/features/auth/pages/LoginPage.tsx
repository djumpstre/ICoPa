import { Alert, Box, Button, Paper, Stack, TextField, Typography } from "@mui/material";
import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { toErrorMessage } from "../../../core/api/errors";
import { useAuthStore } from "../../../stores/auth.store";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const login = useAuthStore((state) => state.login);
  const authError = useAuthStore((state) => state.error);
  const isLoggingIn = useAuthStore((state) => state.isLoggingIn);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const nextPath = (location.state as { from?: string } | null)?.from ?? "/inventory";

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    try {
      await login(username.trim(), password);
      navigate(nextPath, { replace: true });
    } catch (loginError) {
      setError(toErrorMessage(loginError));
    }
  };

  return (
    <Box sx={{ minHeight: "100vh", display: "grid", placeItems: "center", px: 2 }}>
      <Paper component="form" onSubmit={onSubmit} sx={{ width: "100%", maxWidth: 460, p: 3.2 }}>
        <Stack spacing={2}>
          <Box>
            <Typography variant="overline" color="secondary.main">
              ICoPa Hub Access
            </Typography>
            <Typography variant="h5">Sign in</Typography>
          </Box>
          <TextField
            label="Username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            size="small"
          />
          <TextField
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            size="small"
          />
          {(error ?? authError) && <Alert severity="error">{error ?? authError}</Alert>}
          <Button type="submit" variant="contained" disabled={isLoggingIn}>
            {isLoggingIn ? "Signing in..." : "Login"}
          </Button>
        </Stack>
      </Paper>
    </Box>
  );
}
