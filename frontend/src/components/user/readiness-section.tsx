import { useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiClient } from '@/lib/api/client';
import { API_ENDPOINTS } from '@/lib/api/config';
type AthleteCheckinType = "daily" | "pre_event" | "post_event";

interface ReadinessSectionProps {
  userId: string;
}

interface CheckInForm {
  checkin_type: AthleteCheckinType;
  eventType: string;
  eventName: string;
  stress: number;
  energy: number;
  focus: number;
  confidence: number;
  soreness: number;
}

interface AthleteCheckin {
  id: string;
  user_id: string;
  event_type: string;
  event_name: string | null;
  stress_rating: number;
  energy_rating: number;
  focus_rating: number;
  confidence_rating: number;
  soreness_rating: number;
  created_at: string;
}

export function ReadinessSection({ userId }: ReadinessSectionProps) {
  const queryClient = useQueryClient();

  const [form, setForm] = useState<CheckInForm>({
    checkin_type: 'pre_event',
    eventType: 'game',
    eventName: '',
    stress: 5,
    energy: 5,
    focus: 5,
    confidence: 5,
    soreness: 5,
  });

  const [isSaving, setIsSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const {
    data: checkins = [],
    isLoading: isLoadingCheckins,
    isError: isCheckinsError,
  } = useQuery<AthleteCheckin[]>({
    queryKey: ['athlete-checkins', userId],
    queryFn: () =>
      apiClient.get<AthleteCheckin[]>(
        API_ENDPOINTS.userAthleteCheckins(userId)
      ),
    enabled: Boolean(userId),
  });

  function updateNumber(
    field: keyof Pick<
      CheckInForm,
      'stress' | 'energy' | 'focus' | 'confidence' | 'soreness'
    >,
    value: string
  ) {
    const parsed = Number(value);

    setForm((current) => ({
      ...current,
      [field]: Math.min(10, Math.max(1, parsed || 1)),
    }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setIsSaving(true);
    setSaved(false);

    try {
      const createdCheckin = await apiClient.post<AthleteCheckin>(
        API_ENDPOINTS.userAthleteCheckins(userId),
        {
          checkin_type: form.checkin_type,
          event_type: form.eventType,
          event_name: form.eventName.trim() || null,
          stress_rating: form.stress,
          energy_rating: form.energy,
          focus_rating: form.focus,
          confidence_rating: form.confidence,
          soreness_rating: form.soreness,

        }
      );

      queryClient.setQueryData<AthleteCheckin[]>(
        ['athlete-checkins', userId],
        (current = []) => [
          createdCheckin,
          ...current.filter((checkin) => checkin.id !== createdCheckin.id),
        ]
      );

      await queryClient.invalidateQueries({
        queryKey: ['athlete-checkins', userId],
      });

      setSaved(true);
      toast.success('Readiness check-in saved');

      window.setTimeout(() => {
        setSaved(false);
      }, 3000);
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : 'Failed to save readiness check-in';

      toast.error(message);
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-medium">Readiness Check-In</h2>
        <p className="text-sm text-muted-foreground">
          Record how the athlete feels before a game, test, or training session.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="max-w-2xl space-y-5 rounded-xl border p-6"
      >
        <div className="space-y-2">
          <Label htmlFor="checkin-type">Check-in type</Label>

          <select
            id="checkin-type"
            value={form.checkin_type}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                checkin_type: event.target.value as AthleteCheckinType,
              }))
            }
            className="flex h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
          >
            <option value="daily">Daily</option>
            <option value="pre_event">Pre-event</option>
            <option value="post_event">Post-event</option>
          </select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="event-type">Event type</Label>

          <select
            id="event-type"
            value={form.eventType}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                eventType: event.target.value,

              }))
            }
            className="flex h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
          >
            <option value="game">Game</option>
            <option value="practice">Practice</option>
            <option value="test">Test</option>
            <option value="workout">Workout</option>
          </select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="event-name">Event name</Label>

          <Input
            id="event-name"
            value={form.eventName}
            placeholder="Basketball game, 5k erg, sprint session..."
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                eventName: event.target.value,
              }))
            }
          />
        </div>

        <RatingInput
          label="Stress"
          value={form.stress}
          onChange={(value) => updateNumber('stress', value)}
        />

        <RatingInput
          label="Energy"
          value={form.energy}
          onChange={(value) => updateNumber('energy', value)}
        />

        <RatingInput
          label="Focus"
          value={form.focus}
          onChange={(value) => updateNumber('focus', value)}
        />

        <RatingInput
          label="Confidence"
          value={form.confidence}
          onChange={(value) => updateNumber('confidence', value)}
        />

        <RatingInput
          label="Soreness"
          value={form.soreness}
          onChange={(value) => updateNumber('soreness', value)}
        />

        <Button type="submit" disabled={isSaving}>
          {isSaving ? 'Saving...' : saved ? 'Saved!' : 'Save check-in'}
        </Button>
      </form>

      <section className="max-w-4xl space-y-4">
        <div>
          <h3 className="text-lg font-semibold">Recent Check-Ins</h3>
          <p className="text-sm text-muted-foreground">
            Saved readiness entries, newest first.
          </p>
        </div>

        {isLoadingCheckins && (
          <p className="text-sm text-muted-foreground">Loading check-ins...</p>
        )}

        {isCheckinsError && (
          <p className="text-sm text-destructive">Failed to load check-ins.</p>
        )}

        {!isLoadingCheckins && !isCheckinsError && checkins.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No check-ins have been saved yet.
          </p>
        )}

        <div className="space-y-3">
          {checkins.map((checkin) => (
            <div key={checkin.id} className="rounded-xl border p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-medium">
                    {checkin.event_name || checkin.event_type}
                  </p>

                  {checkin.event_name && (
                    <p className="text-sm capitalize text-muted-foreground">
                      {checkin.event_type}
                    </p>
                  )}
                </div>

                <p className="text-sm text-muted-foreground">
                  {new Date(checkin.created_at).toLocaleString()}
                </p>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-5">
                <Metric label="Stress" value={checkin.stress_rating} />
                <Metric label="Energy" value={checkin.energy_rating} />
                <Metric label="Focus" value={checkin.focus_rating} />
                <Metric label="Confidence" value={checkin.confidence_rating} />
                <Metric label="Soreness" value={checkin.soreness_rating} />
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

interface RatingInputProps {
  label: string;
  value: number;
  onChange: (value: string) => void;
}

function RatingInput({ label, value, onChange }: RatingInputProps) {
  const id = label.toLowerCase();

  return (
    <div className="space-y-2">
      <Label htmlFor={id}>
        {label}: {value}/10
      </Label>

      <Input
        id={id}
        type="range"
        min={1}
        max={10}
        step={1}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

interface MetricProps {
  label: string;
  value: number;
}

function Metric({ label, value }: MetricProps) {
  return (
    <div>
      <p className="text-muted-foreground">{label}</p>
      <p className="font-medium">{value}/10</p>
    </div>
  );
}
